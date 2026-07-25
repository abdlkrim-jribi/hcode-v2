"""HCode v2 agent factory."""

from __future__ import annotations

import logging
import os
import platform
import sys
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

from hcode_v2.agent.harness_notes import HarnessNotesMiddleware
from hcode_v2.agent.mcp_env import env_injecting_client_factory
from hcode_v2.agent.path_containment import _PathContainmentMiddleware
from hcode_v2.agent.project_map import build_project_map
from hcode_v2.provider.fallback import maybe_wrap
from hcode_v2.tools.lsp_tools import verify_diagnostics_addendum
from hcode_v2.utils.config import Config

logger = logging.getLogger(__name__)

# ── Harness tool hygiene (P1 path contradiction / T1 duplicate tools / T3 ask_user hang) ──
#
# The vendored base stack (FilesystemMiddleware, TodoListMiddleware) always
# injects its own ls/read_file/write_file/edit_file/glob/grep/execute/
# write_todos. Two DIFFERENT outcomes happen depending on whether HCode's own
# same-named tool exists:
#   - ls/glob/grep: HCode registers tools with the SAME names. create_agent
#     merges [middleware tools] + [explicit tools=] and de-dupes by name with
#     explicit tools winning (HCode's own tools= list is appended AFTER the
#     middleware-injected list before the name->tool dict is built) — so
#     HCode's ls/glob/grep (with correct containment) are ALREADY what's bound
#     today. Adding these names to the exclusion set would DELETE them
#     entirely (only one survives to filter against), not "pick a side" — so
#     they are deliberately NOT excluded here.
#   - read_file / execute / write_todos: DIFFERENT names from HCode's read /
#     bash / todo_read+todo_write, so there is no name collision — BOTH
#     copies survive and reach the model, doubling the menu for the same
#     capability with a DIFFERENT (contradictory) path convention. These ARE
#     excluded below.
# _VERIFY_READONLY_TOOLS (pev.py, vendored) already whitelists BOTH "read_file"
# and "read" defensively for exactly this reason; excluding "read_file" still
# leaves "read" (HCode's own, never excluded) available in the verify phase —
# reconciled, not broken. See tests/test_tool_name_contracts.py.
#
# Known trade-off (not addressed here): the vendored FilesystemMiddleware
# prompt tells the model to use `read_file` to inspect large tool results the
# SummarizationMiddleware has offloaded to disk. That offload always goes
# through the backend's OWN (virtual-mode-aware) path resolution, which is a
# separate scheme from HCode's `_resolve_path`. Whether HCode's `read` can
# recover an offloaded result at the same nominal path is unverified here —
# a narrow edge case (only reachable once a single tool result is large enough
# to trigger mid-conversation summarization) outside this fix's scope.
_EXCLUDED_BUILTIN_TOOLS: frozenset[str] = frozenset({
    "edit_file", "write_file",  # replaced by hcode's edit/write (existing)
    "read_file",                # replaced by hcode's read
    "execute",                  # replaced by hcode's bash
    "write_todos",              # replaced by hcode's todo_read/todo_write
})

# P1: state the ONE path convention authoritatively, overriding the vendored
# fs prompt's contradictory "must start with /" line, and note the tool
# renames above so nothing dangles. See harness_notes.py for WHY this lands
# after the vendored prompt text on every call (ordering guarantee).
_HARNESS_CLARITY_NOTE = (
    "## Path & Tool Convention (authoritative — overrides any earlier note)\n\n"
    "Ignore any instruction above claiming file paths must start with `/`. The "
    "actual rule for every file tool in this project:\n"
    "- Use a path RELATIVE to the working directory (e.g. `src/app.py`), or\n"
    "- A full absolute path already inside the working directory.\n"
    "A leading-slash path like `/app.py` is NOT a filesystem root here — it is "
    "re-homed under the working directory.\n\n"
    "Also ignore any mention of `read_file`, `execute`, or `write_todos` — "
    "they are not available. Use `read` to read files (including any large "
    "tool result saved to disk), `bash` to run shell commands, and "
    "`todo_read`/`todo_write` to manage the task list."
)


def _is_interactive_stdin() -> bool:
    """True when stdin is a real TTY (the CLI's interactive session).

    False for anything non-interactive — critically, the daemon's stdin IS the
    JSON-RPC channel (server.py's run() loop reads it for incoming requests).
    ask_user/confirm block on input(); calling either mid-daemon-run would
    either hang the run or consume the next RPC request as the "answer" (T3).
    Checked once at agent-build time — a process's stdin tty-ness is fixed for
    its whole lifetime, so this never needs re-checking mid-session.
    """
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _build_one_model(model_name: str, config: Config, max_tokens: int,
                     anthropic_key: str | None, inner_retries: int | None,
                     base_url_override: str | None = None,
                     api_key_override: str | None = None):
    """Build a single chat model client for ``model_name``.

    ``inner_retries`` controls the provider SDK's own retry count: ``None`` leaves
    the client default (the pre-existing single-model behaviour — ChatOpenAI
    defaults to 2), and ``0`` disables SDK retries so the ResilientChatModel is the
    single source of retry/backoff truth on the fallback path. The model is then
    wrapped with ``JsonToolCallWrapper`` per ``HCODE_TOOLCALL_MODE``.

    ``base_url_override``/``api_key_override`` (cross-provider fallback, M6) point
    this ONE client at a different provider than the active Config — always via
    the OpenAI-compatible branch. Absent (the default) → exact prior behaviour.
    """
    if base_url_override or api_key_override:
        from langchain_openai import ChatOpenAI
        kw = {} if inner_retries is None else {"max_retries": inner_retries}
        base_model = ChatOpenAI(
            model=model_name, max_tokens=max_tokens,
            api_key=api_key_override or config.api_key,
            base_url=base_url_override or config.base_url, **kw,
        )
        return maybe_wrap(base_model, config.toolcall_mode)
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


def _parse_fallback_entry(entry: str) -> tuple[str, str | None]:
    """Split a fallback entry into ``(model, provider_alias | None)``.

    ``"openai/gpt-oss-20b"``            → same-provider (today's #104 form).
    ``"gpt-oss-120b@cerebras"``         → the model on the named provider, whose
    endpoint/key come from ``HCODE_PROVIDER_<ALIAS>_BASE_URL/_API_KEY``. Split on
    the LAST ``@`` (model slugs contain ``/`` but never ``@``).
    """
    if "@" not in entry:
        return entry, None
    model, _, provider = entry.rpartition("@")
    model, provider = model.strip(), provider.strip()
    if not model or not provider:
        return entry, None  # malformed — treat as a plain same-provider slug
    return model, provider


def _provider_overrides(provider: str) -> tuple[str, str] | None:
    """Resolve ``(base_url, api_key)`` for a provider alias from the environment.

    Reads ``HCODE_PROVIDER_<ALIAS>_BASE_URL`` and ``..._API_KEY`` (alias upper-
    cased, ``-``→``_``). Returns ``None`` when either is missing — the caller
    SKIPS that entry with a warning rather than building a client that can only
    fail (or, worse, silently reusing the wrong provider's key).
    """
    alias = provider.upper().replace("-", "_")
    base_url = os.getenv(f"HCODE_PROVIDER_{alias}_BASE_URL", "").strip()
    api_key = os.getenv(f"HCODE_PROVIDER_{alias}_API_KEY", "").strip()
    if not base_url or not api_key:
        return None
    return base_url, api_key


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

    ``HCODE_FAKE_MODEL`` (verification infra, C8): when set to a scripted-turn
    JSON file path AND ``HCODE_ALLOW_FAKE=1`` is ALSO set, returns a
    ``ScriptedChatModel`` loaded from that file instead of building any real
    provider client — no key, no network, no quota. This is what lets
    ``scripts/probe_daemon.py`` drive the REAL daemon subprocess (real JSON-RPC
    transport, real event bridge, real PEV/plan-review/post-edit-gate
    middleware) with zero API dependency, e.g. in CI. TWO env vars are
    required specifically so this can never activate by accident — a stray
    ``HCODE_FAKE_MODEL`` left in an environment does nothing without the
    second, explicit opt-in. Checked FIRST, before any other config
    resolution, so a fake-model run never even touches ``Config.from_env()``
    (no key needs to exist at all). See ``tests/helpers/scripted_model.py``.
    """
    fake_script = os.getenv("HCODE_FAKE_MODEL", "").strip()
    if fake_script and os.getenv("HCODE_ALLOW_FAKE", "").strip().lower() in ("1", "true", "yes", "on"):
        import sys as _sys
        helpers_dir = Path(__file__).resolve().parents[3] / "tests" / "helpers"
        if str(helpers_dir) not in _sys.path:
            _sys.path.insert(0, str(helpers_dir))
        from scripted_model import fresh_scripted_model, load_script
        return fresh_scripted_model(load_script(fake_script))

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
    #
    # Cross-provider entries (M6): a fallback entry "model@provider" builds its
    # client against HCODE_PROVIDER_<PROVIDER>_BASE_URL/_API_KEY instead of the
    # active Config — the M5 bake-off proved gpt-oss-120b runs on BOTH Groq and
    # Cerebras, so the SAME model can dual-home across providers. An entry whose
    # provider env is missing is SKIPPED with a warning (never silently built
    # against the wrong provider's key). Plain entries keep #104's same-provider
    # behaviour byte-for-byte.
    from hcode_v2.provider.resilient import (
        ResilientChatModel, default_call_timeout, default_retry_config,
    )
    clients, names = [], []
    seen: set[str] = set()

    clients.append(_build_one_model(primary, config, max_tokens, anthropic_key, inner_retries=0))
    names.append(primary)
    seen.add(primary)

    for entry in fallback_models or []:
        entry = (entry or "").strip()
        if not entry or entry in seen:
            continue
        model_name, provider = _parse_fallback_entry(entry)
        if provider is None:
            if model_name == primary:
                continue
            clients.append(_build_one_model(model_name, config, max_tokens, anthropic_key, inner_retries=0))
            names.append(model_name)
        else:
            overrides = _provider_overrides(provider)
            if overrides is None:
                logger.warning(
                    "Fallback entry %r skipped: HCODE_PROVIDER_%s_BASE_URL/_API_KEY not set",
                    entry, provider.upper().replace("-", "_"),
                )
                continue
            base_url, api_key = overrides
            clients.append(_build_one_model(
                model_name, config, max_tokens, anthropic_key, inner_retries=0,
                base_url_override=base_url, api_key_override=api_key,
            ))
            names.append(f"{model_name} @ {provider}")
        seen.add(entry)

    mr, bb = default_retry_config(max_retries, backoff_base)
    return ResilientChatModel(
        clients=clients, model_names=names,
        max_retries=mr, backoff_base=bb, on_fallback=on_fallback,
        # First-token stall budget (HCODE_FALLBACK_TIMEOUT, default 90s): the M5
        # bake-off showed providers can fail by HANGING with zero bytes and no
        # 429 — only a client-side timeout converts that into a failover. Only
        # active on this opt-in path; the no-fallback client above has none.
        call_timeout=default_call_timeout(),
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

    Kept declarative-facts-only (no file listing) on purpose — the ``<project_map>``
    tree lives in a SEPARATE block assembled by ``_build_context_prompt``.
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


def _build_context_prompt(work_dir: str) -> str:
    """The full USER system-prompt segment: the ``<env>`` facts block plus, when
    the working directory yields one, a ``<project_map>`` (bounded tree + manifest
    + README) so the PLAN phase can name REAL files instead of fabricating them —
    the audit's #1 finding.

    Built ONCE at agent-build time. The agent is cached per work_dir, so the map
    refreshes on a folder switch (cache eviction) and is NOT rebuilt per task. An
    empty/unreadable dir → ``build_project_map`` returns ``""`` → only the env
    block is used, exactly the pre-existing (map-less) behaviour (zero regression).
    """
    env_block = _build_env_block(work_dir)
    project_map = build_project_map(str(Path(work_dir).resolve()))
    return f"{env_block}\n\n{project_map}" if project_map else env_block


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
    plan_review: bool = False,
    force_plan: bool = False,
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

    ``plan_review`` (opt-in HITL) inserts ``PlanReviewMiddleware`` so the agent
    pauses at the plan→execute boundary for human accept/reject. ``False`` (the
    default) omits it entirely → unchanged run. Requires PEV (``enable_pev``).

    ``force_plan`` (composer "Plan" mode) inserts ``ForcePlanMiddleware`` so the
    task runs the full plan→execute→verify arc even when PEV's classifier would
    have routed it to the single-pass ``fast`` phase. ``False`` (the default and
    the "Fast"/absent case) omits it entirely → the classifier's verdict stands →
    unchanged run. Requires PEV (``enable_pev``).
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
        # Plan review (opt-in): pause at the plan→execute boundary for human
        # accept/reject. Added ONLY when plan_review=True AND PEV is on (it keys
        # off PEV's phase contract). Absent by default → zero behaviour change.
        # Placed right after PEV so it sees the phase PEV set. Does NOT edit
        # vendored pev.py — reads _pev_phase/_pev_plan only.
        if plan_review:
            from hcode_v2.agent.plan_review import PlanReviewMiddleware
            middleware.append(PlanReviewMiddleware())
        # Force-plan (opt-in "Plan" mode): pin the initial phase to "plan" so the
        # full arc runs for any task, overriding the classifier's fast routing.
        # Added ONLY when force_plan=True AND PEV is on. Registered after PEV so
        # its before_agent override merges over PEV's classification. Absent by
        # default → the classifier's verdict stands → zero behaviour change.
        # Composes with plan_review: both set _pev_phase="plan" (idempotent).
        if force_plan:
            from hcode_v2.agent.force_plan import ForcePlanMiddleware
            middleware.append(ForcePlanMiddleware())
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
    # Also strips the OTHER differently-named duplicates (read_file/execute/
    # write_todos — see _EXCLUDED_BUILTIN_TOOLS above for why ls/glob/grep are
    # NOT in this set) and, in a non-interactive (daemon) run, ask_user/confirm
    # (T3 — their input() would hang the run or eat the next RPC as stdin is
    # the JSON-RPC channel there, not a TTY). Must be appended LATE: it filters
    # tools at model-call time and has to run after the tool-injecting
    # middleware (FilesystemMiddleware/TodoListMiddleware) to strip the
    # builtins they inject.
    # NOTE: imports a PRIVATE deepagents symbol (_tool_exclusion) — intentional;
    # revisit on a deepagents re-vendor if that module path changes.
    excluded_tools = set(_EXCLUDED_BUILTIN_TOOLS)
    if not _is_interactive_stdin():
        excluded_tools |= {"ask_user", "confirm"}
    # Prompt slimming (HCODE_SLIM_PROMPT: 0=off, 1=default, max=free-tier lane):
    # level>=1 drops the unused `task` subagent rider (1.7k schema + 0.5k prompt
    # per call); level max also drops the notebook/web trios to fit per-request
    # caps like Groq free's 8k. See prompt_slim.py for the measured rationale.
    from hcode_v2.agent.prompt_slim import PromptSlimMiddleware, slim_excluded_tools, slim_level
    _slim = slim_level()
    excluded_tools |= slim_excluded_tools(_slim)
    middleware.append(_ToolExclusionMiddleware(excluded=frozenset(excluded_tools)))
    # P1: one authoritative path/tool-naming correction, appended after every
    # vendored prompt segment above (ordering guaranteed — see harness_notes.py).
    middleware.append(HarnessNotesMiddleware(_HARNESS_CLARITY_NOTE))
    # Post-edit LSP gate: after every successful write/edit, run the language
    # server on JUST the edited file and — on real ERRORS only — append the
    # diagnostics to the tool result so the model fixes them in its natural next
    # turn. This is the phase-independent type-check safety net: fast-mode edits
    # (most ordinary tasks) get checked without paying for plan+verify model
    # round-trips. Clean edit → result untouched → zero added model calls.
    # Structurally cannot double the Verify-lane provider (verify strips mutating
    # tools; the gate also skips on _pev_phase=="verify" defensively). No server
    # or non-code file → provider returns None → pass-through (zero regression).
    # Kill-switch: HCODE_POST_EDIT_LSP=0/false/off omits the middleware entirely.
    # HCode-side; deepagents untouched.
    if os.getenv("HCODE_POST_EDIT_LSP", "1").strip().lower() not in ("0", "false", "off"):
        from hcode_v2.agent.post_edit_lsp import PostEditLspMiddleware
        middleware.append(PostEditLspMiddleware())
    # Containment at the EXECUTION seam: re-home the path arg of every file tool
    # (builtins AND hcode's) through _resolve_path before the tool runs, so a
    # bound-but-uncontained builtin the model calls from memory (e.g. write_file
    # with "/x.py") can't escape the working dir. Menu exclusion above hides
    # most duplicates; this guards whatever tool actually runs (ls/glob/grep,
    # hcode's own, or any excluded builtin still named from memory). HCode-side;
    # deepagents untouched.
    middleware.append(_PathContainmentMiddleware())
    # Prompt slimming, LAST so it sees the fully-assembled system message (same
    # ordering guarantee harness_notes.py documents): phase-scopes the skills
    # section, excises vendored sections describing excluded tools, and (at
    # level max) caps the per-phase completion budget for providers that count
    # max_tokens toward a per-request limit. "0" → absent → byte-identical
    # pre-slim behaviour. HCode-side; deepagents untouched.
    if _slim != "0":
        middleware.append(PromptSlimMiddleware(level=_slim))

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
        # prompts below, so this orientation context (env facts + project map) is
        # present in every phase — critically the PLAN phase, which must name real
        # files.
        system_prompt=_build_context_prompt(resolved_work_dir),
    )
