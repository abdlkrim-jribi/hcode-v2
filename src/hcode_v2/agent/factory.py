"""HCode v2 agent factory."""

from __future__ import annotations

import logging
import os
import platform
from datetime import date
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware
from deepagents.middleware.pev import PEVMiddleware
from deepagents.middleware.safety_guard import SafetyGuardMiddleware
from deepagents.middleware.workflows import WorkflowMiddleware
from deepagents.mcp.bridge import MCPToolRegistry
from deepagents.mcp.client import MCPClientManager

from hcode_v2.agent.mcp_env import env_injecting_client_factory
from hcode_v2.agent.path_containment import _PathContainmentMiddleware
from hcode_v2.provider.fallback import maybe_wrap
from hcode_v2.tools.lsp_tools import verify_diagnostics_addendum
from hcode_v2.utils.config import Config

logger = logging.getLogger(__name__)


def _build_model():
    """Build the LangChain chat model from environment config.

    Model identity, endpoint, and tool-calling strategy come from
    ``Config.from_env()``, which resolves:

    - model name: ``HCODE_MODEL_NAME`` -> ``HCODE_MODEL`` -> ``"gpt-4o-mini"``
    - api_key:    ``HCODE_MODEL_API_KEY`` -> ``OPENAI_API_KEY``
    - base_url:   ``HCODE_MODEL_BASE_URL`` -> ``OPENAI_BASE_URL``
    - mode:       ``HCODE_TOOLCALL_MODE``  -> ``"native"`` (default)

    ``ANTHROPIC_API_KEY`` selects ``ChatAnthropic`` when set and no
    OpenAI-compatible key is resolved.  ``HCODE_MAX_TOKENS`` caps output
    tokens (default 8000 — reasoning models like gpt-oss spend this budget
    on reasoning tokens before visible text; 2000 truncated plan-phase
    responses to empty once the skills section grew the prompt).

    When ``HCODE_TOOLCALL_MODE`` is ``"json"`` or ``"auto"``, the model is
    wrapped with ``JsonToolCallWrapper`` so the agent degrades gracefully on
    endpoints that lack native function-calling support.  Run
    ``scripts/probe_model.py`` against the endpoint to determine the right mode.
    """
    config = Config.from_env()
    max_tokens = int(os.getenv("HCODE_MAX_TOKENS", "8000"))
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if anthropic_key and not config.api_key:
        from langchain_anthropic import ChatAnthropic
        base_model = ChatAnthropic(
            model=config.model,
            max_tokens=max_tokens,
            api_key=anthropic_key,
        )
    else:
        from langchain_openai import ChatOpenAI
        base_model = ChatOpenAI(
            model=config.model,
            max_tokens=max_tokens,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    return maybe_wrap(base_model, config.toolcall_mode)


def _build_env_block(work_dir: str) -> str:
    """Build the OpenCode-style ``<env>`` orientation block for the system prompt.

    Declarative facts (working directory, platform, git-repo flag, date) plus a
    one-line path convention, so the model knows where it is and stops wandering
    the real filesystem (``ls /``) in the execute phase. The path is the resolved
    absolute working directory — the form the shell/execute tools and HCode's own
    file tools actually use. The path-convention line matters because the backend
    runs with ``virtual_mode=False`` (real OS paths): a bare ``/foo`` would
    resolve to the drive root, so the model is told to use relative or full
    absolute paths.
    """
    root = Path(work_dir).resolve()
    is_git = "yes" if (root / ".git").exists() else "no"
    return (
        "<env>\n"
        f"Working directory: {root}\n"
        f"Platform: {platform.system()}\n"
        f"Is git repo: {is_git}\n"
        f"Today's date: {date.today().isoformat()}\n"
        "Write file paths relative to the working directory (e.g. temps5.py) or "
        "as a full absolute path; do not use a leading-slash root path like "
        "/temps5.py.\n"
        "</env>"
    )


async def create_hcode_agent(
    skills_dir: str = ".hcode/skills",
    workflows_dir: str = ".hcode/workflows",
    mcp_config: str = ".hcode/mcp_config.json",
    enable_pev: bool = True,
    enable_safety: bool = True,
    session_id: str = "default",
    persist: bool = True,
    work_dir: str | None = None,
    interrupt_on: dict | None = None,
):
    """Assemble the full HCode v2 agent from environment config."""
    from deepagents.checkpointers.sqlite import HCodeSQLiteCheckpointer
    from langgraph.checkpoint.memory import MemorySaver

    if persist:
        Path(".hcode/sessions").mkdir(parents=True, exist_ok=True)
        checkpointer = HCodeSQLiteCheckpointer(
            db_path=f".hcode/sessions/{session_id}.db"
        )
    else:
        checkpointer = MemorySaver()

    model = _build_model()

    # Create backend ONCE — shared by SummarizationMiddleware and agent.
    # virtual_mode=False: the deepagents builtins use REAL OS paths anchored at
    # root_dir — the SAME directory hcode's own file tools resolve against
    # (get_root_dir reads HCODE_ROOT_DIR). With virtual_mode=True the backend
    # remapped "/" to work_dir and (via the Windows-abs rejection + the
    # "/workspace/…" prompt example) nudged the model into emitting
    # "/workspace/temps5.py", which hcode's write then joined under the cwd as a
    # phantom <cwd>/workspace/ subdir. Real paths make both tool families agree.
    # inherit_env=True gives the execute tool the parent environment (PATH
    # included) — without it the backend runs commands with an EMPTY env, so
    # `python` / `pytest` are never found and the execute phase dead-ends.
    # Inheriting the full env is a conscious trade-off for a local dev CLI.
    resolved_work_dir = work_dir or os.getcwd()
    # Align HCode's registry tools (get_root_dir reads HCODE_ROOT_DIR) with the
    # backend root and the <env> block, so the directory the model is TOLD about
    # is the one its file/shell tools actually resolve against.
    os.environ["HCODE_ROOT_DIR"] = str(Path(resolved_work_dir).resolve())
    # PYTHONIOENCODING/PYTHONUTF8 force Python children (pytest) to emit UTF-8
    # even when stdout is a pipe — otherwise they write the Windows ANSI code
    # page (cp1252/cp1256) and the reader's UTF-8 decode hits invalid bytes.
    backend = LocalShellBackend(
        root_dir=resolved_work_dir,
        virtual_mode=False,
        inherit_env=True,
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )

    middleware = []
    if enable_pev:
        # W3.3: give Verify an LSP diagnostics provider. It self-gates — with no
        # language server installed it returns None and Verify behaves as before.
        middleware.append(PEVMiddleware(diagnostics_provider=verify_diagnostics_addendum))
    if enable_safety:
        middleware.append(SafetyGuardMiddleware())
    middleware.append(HCodeSkillsMiddleware(skills_dir=skills_dir))
    middleware.append(WorkflowMiddleware(workflows_dir=workflows_dir))
    # Route the model OFF the deepagents builtin mutating file tools and onto
    # hcode's own edit/write/multi_edit, which return response_format=
    # "content_and_artifact" so a structured diff flows to the live renderer.
    # edit_file/write_file are the redundant mutating builtins we replace; the
    # read-side builtins (read_file/ls/glob/grep) stay — FilesystemMiddleware
    # scaffolding relies on its read path. Must be appended LAST: it filters
    # tools at model-call time and has to run after the tool-injecting
    # middleware (FilesystemMiddleware) to strip the builtins they inject.
    # NOTE: imports a PRIVATE deepagents symbol (_tool_exclusion) — intentional;
    # revisit on a deepagents re-vendor if that module path changes.
    middleware.append(_ToolExclusionMiddleware(excluded=frozenset({"edit_file", "write_file"})))
    # Containment at the EXECUTION seam: re-home the path arg of every file tool
    # (builtins AND hcode's) through _resolve_path before the tool runs, so a
    # bound-but-uncontained builtin the model calls from memory (e.g. write_file
    # with "/x.py") can't escape the working dir. Menu exclusion above hides the
    # mutating builtins; this guards the rest (read_file/ls/glob/grep) and any
    # excluded builtin still named from memory. HCode-side; deepagents untouched.
    middleware.append(_PathContainmentMiddleware())

    mcp_tools = []
    # _client_factory injects each server's env_required secrets from os.environ
    # (e.g. GITHUB_TOKEN from .env) into the per-server env before connect — the
    # MCP SDK only forwards a safelist otherwise, so the token never reached the
    # subprocess. HCode-side hook; libs/deepagents stays untouched.
    manager = MCPClientManager(config_path=mcp_config, _client_factory=env_injecting_client_factory)
    if manager.is_configured:
        try:
            # connect_all() is async; build_tools() is a synchronous static
            # method returning a list — do NOT await it.
            await manager.connect_all()
            mcp_tools = MCPToolRegistry.build_tools(manager)
        except Exception as e:
            logger.warning("MCP failed: %s", e)

    # All 31 tools are ~1213 tokens total — safe to pass all
    from hcode_v2.tools.registry import get_all_tools
    all_tools = get_all_tools() + mcp_tools

    return create_deep_agent(
        model=model,
        tools=all_tools,
        middleware=middleware,
        backend=backend,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on,
        # Prepended above BASE_AGENT_PROMPT (USER segment); PEV appends its phase
        # prompts below, so this orientation block is present in every phase.
        system_prompt=_build_env_block(resolved_work_dir),
    )
