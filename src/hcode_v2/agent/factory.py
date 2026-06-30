"""HCode v2 agent factory."""

from __future__ import annotations

import logging
import os
import platform
from datetime import date
from pathlib import Path
from typing import Callable

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


def _build_one_model(model_name: str, config: Config, max_tokens: int,
                     anthropic_key: str | None, inner_retries: int | None):
    """Build a single chat model client for ``model_name``.

    ``inner_retries`` controls the provider SDK's own retry count: ``None`` leaves
    the client default (the pre-existing single-model behaviour — ChatOpenAI
    defaults to 2), and ``0`` disables SDK retries so the ResilientChatModel is the
    single source of retry/backoff truth on the fallback path. The model is then
    wrapped with ``JsonToolCallWrapper`` per ``HCODE_TOOLCALL_MODE``.
    """
    if anthropic_key and not config.api_key:
        from langchain_anthropic import ChatAnthropic
        kw = {} if inner_retries is None else {"max_retries": inner_retries}
        base_model = ChatAnthropic(
            model=model_name, max_tokens=max_tokens, api_key=anthropic_key, **kw,
        )
    else:
        from langchain_openai import ChatOpenAI
        kw = {} if inner_retries is None else {"max_retries": inner_retries}
        base_model = ChatOpenAI(
            model=model_name, max_tokens=max_tokens,
            api_key=config.api_key, base_url=config.base_url, **kw,
        )
    return maybe_wrap(base_model, config.toolcall_mode)


def _build_model(
    model_override: str | None = None,
    fallback_models: list[str] | None = None,
    max_retries: int | None = None,
    backoff_base: float | None = None,
    on_fallback: "Callable[[str, str], None] | None" = None,
):
    """Build the LangChain chat model from environment config.

    Model identity, endpoint, and tool-calling strategy come from
    ``Config.from_env()``, which resolves:

    - model name: ``HCODE_MODEL_NAME`` -> ``HCODE_MODEL`` -> ``"gpt-4o-mini"``
    - api_key:    ``HCODE_MODEL_API_KEY`` -> ``OPENAI_API_KEY``
    - base_url:   ``HCODE_MODEL_BASE_URL`` -> ``OPENAI_BASE_URL``
    - mode:       ``HCODE_TOOLCALL_MODE``  -> ``"native"`` (default)

    ``model_override`` (GUI model selection) replaces ONLY the model name — the
    api_key, base_url, and tool-calling mode stay from the environment (same
    provider account/endpoint, different model). ``None`` (the default and the
    CLI path) preserves the exact prior behaviour: the configured model name.

    ``fallback_models`` (429 resilience) is the ordered list of OTHER model names
    to fall back to when the primary is rate-limited. When it is falsy (the
    DEFAULT), this returns exactly the prior single client — zero behaviour
    change, no retry wrapper, no added latency. When it is non-empty, the primary
    + fallbacks are wrapped in a ``ResilientChatModel`` that retries transient
    errors with backoff then advances to the next model (see provider/resilient).
    ``on_fallback(from_name, to_name)`` is called when a switch happens so the UI
    can surface it.

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
    primary = model_override or config.model
    max_tokens = int(os.getenv("HCODE_MAX_TOKENS", "8000"))
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    # DEFAULT path (fallback off): identical to the prior behaviour — one client,
    # the SDK's own retry default, no resilience wrapper, no extra latency.
    if not fallback_models:
        return _build_one_model(primary, config, max_tokens, anthropic_key, inner_retries=None)

    # Resilience path: primary first, then the distinct fallbacks. Each inner
    # client's SDK retries are disabled (inner_retries=0) so ResilientChatModel
    # owns retry/backoff; it then handles fall-through to the next model.
    from hcode_v2.provider.resilient import ResilientChatModel, default_retry_config
    names = [primary] + [m for m in fallback_models if m and m != primary]
    clients = [
        _build_one_model(n, config, max_tokens, anthropic_key, inner_retries=0)
        for n in names
    ]
    mr, bb = default_retry_config(max_retries, backoff_base)
    return ResilientChatModel(
        clients=clients, model_names=names,
        max_retries=mr, backoff_base=bb, on_fallback=on_fallback,
    )


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
    skills_dir: str | None = None,
    workflows_dir: str = ".hcode/workflows",
    mcp_config: str = ".hcode/mcp_config.json",
    enable_pev: bool = True,
    enable_safety: bool = True,
    session_id: str = "default",
    persist: bool = True,
    work_dir: str | None = None,
    interrupt_on: dict | None = None,
    active_skills: list[str] | None = None,
    model: str | None = None,
    fallback_models: list[str] | None = None,
    on_fallback: "Callable[[str, str], None] | None" = None,
):
    """Assemble the full HCode v2 agent from environment config.

    ``model`` (GUI model selection) overrides only the model NAME for this agent;
    ``None`` (the default and the CLI path) uses the configured model — zero
    behaviour change when the argument is absent.

    ``fallback_models`` (429 resilience) + ``on_fallback`` are forwarded to
    ``_build_model``. Falsy ``fallback_models`` (the default) keeps the exact
    single-client behaviour; a non-empty list wraps the model so a rate-limited
    primary retries then falls back to the next model. ``on_fallback`` is invoked
    on a switch so the daemon can surface it to the UI.
    """
    from deepagents.checkpointers.sqlite import HCodeSQLiteCheckpointer
    from langgraph.checkpoint.memory import MemorySaver

    if persist:
        Path(".hcode/sessions").mkdir(parents=True, exist_ok=True)
        checkpointer = HCodeSQLiteCheckpointer(
            db_path=f".hcode/sessions/{session_id}.db"
        )
    else:
        checkpointer = MemorySaver()

    chat_model = _build_model(
        model_override=model,
        fallback_models=fallback_models,
        on_fallback=on_fallback,
    )

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
    # skills_dir=None → resolve the built-in skills install-relative (CWD-independent)
    # so the agent sees them no matter where it runs (incl. after the daemon chdir's
    # into work_dir). An explicit skills_dir wins.
    from hcode_v2.skills_path import builtin_skills_dir
    resolved_skills_dir = skills_dir if skills_dir is not None else str(builtin_skills_dir())
    # active_skills: None or [] → load ALL skills (no footgun from accidental empty list).
    # A non-empty list → load only the named skills via SelectiveSkillsMiddleware, which
    # subclasses the vendored HCodeSkillsMiddleware without editing vendored code.
    effective = active_skills if active_skills else None
    if effective:
        from hcode_v2.agent.selective_skills import SelectiveSkillsMiddleware
        middleware.append(SelectiveSkillsMiddleware(
            skills_dir=resolved_skills_dir, allow=frozenset(effective),
        ))
    else:
        middleware.append(HCodeSkillsMiddleware(skills_dir=resolved_skills_dir))
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
        model=chat_model,
        tools=all_tools,
        middleware=middleware,
        backend=backend,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on,
        # Prepended above BASE_AGENT_PROMPT (USER segment); PEV appends its phase
        # prompts below, so this orientation block is present in every phase.
        system_prompt=_build_env_block(resolved_work_dir),
    )
