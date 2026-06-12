"""PEV (Plan-Execute-Verify) middleware for structured task execution."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr, hook_config
from langchain_core.messages import AIMessage
from typing_extensions import TypedDict

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.safety_guard import _FILE_TOOLS as _SAFETY_FILE_TOOLS
from deepagents.middleware.task_classifier import TaskClassifier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.runtime import Runtime

    # Optional hook (W3.3): given the files edited this task, return a verify-prompt
    # addendum if a language server found ERRORS, else None. Injected by the host
    # app (hcode_v2) so this middleware stays standalone and LSP-agnostic.
    DiagnosticsProvider = Callable[[list[str]], Awaitable[str | None]]

logger = logging.getLogger(__name__)

_PLAN_PROMPT: str = """\
## PEV Planning Phase

You are in the PLANNING phase. No tools are available; do NOT attempt any \
action and do NOT write code.

Respond with a NUMBERED step-by-step plan. Every step names the exact file(s) \
it touches and the tool(s) it will use.

You MUST end your response with this exact line, alone on its own line:
PLAN COMPLETE"""

_EXECUTE_PROMPT: str = """\
## PEV Execution Phase

Execute the plan from the previous step one step at a time.
Use available tools to accomplish each step.

When all steps are complete and results are correct, output this marker alone on its own line:
EXECUTION COMPLETE"""

# Tool names listed here must match _VERIFY_READONLY_TOOLS below.
_VERIFY_PROMPT: str = """\
## PEV Verification Phase

You are in the VERIFICATION phase. Only these read-only tools are available: \
read_file, read, ls, glob, grep. Do NOT attempt execute or any other tool — \
they are not available in this phase and will fail.

Base your verdict on READING the created/modified files and checking them \
against the plan. Do not modify anything.

You MUST end your response with exactly one of these lines, alone on its own line:
VERIFIED OK
ISSUES FOUND: <what is wrong>"""

_FAST_PROMPT: str = """\
## PEV Fast Mode

Complete the task in a single response using the available tools."""

_PHASE_PROMPTS: dict[str, str] = {
    "plan": _PLAN_PROMPT,
    "execute": _EXECUTE_PROMPT,
    "verify": _VERIFY_PROMPT,
    "fast": _FAST_PROMPT,
}

# read_file is the deepagents filesystem-middleware reader; read is the hcode
# registry reader. Both stacks can be present, so verify whitelists both.
_VERIFY_READONLY_TOOLS: frozenset[str] = frozenset({"read_file", "read", "ls", "glob", "grep"})
_PHASE_MARKERS: tuple[str, ...] = ("PLAN COMPLETE", "EXECUTION COMPLETE", "VERIFIED OK", "ISSUES FOUND")
"""Completion/verdict markers; a breaker exit without any of these gets a synthesized status."""
_MAX_ERRORS: int = 3
_MAX_ITERATIONS: int = 5
"""Per-phase model-call cap for the plan and verify phases."""
_MAX_EXECUTE_ITERATIONS: int = 15
"""Per-phase model-call cap for execute, which carries the multi-step tool work."""
_HASH_WINDOW: int = 5
_LOOP_THRESHOLD: int = 3

_classifier: TaskClassifier = TaskClassifier()


def _collect_modified_files(state: PEVState) -> list[str]:
    """Files edited this task, read from the message history (W3.3).

    Scans AI ``tool_calls`` for the file-writing tools (the same set SafetyGuard
    tracks, ``_SAFETY_FILE_TOOLS``) and returns their ``path`` args, de-duplicated
    in first-seen order.  This is derived from messages — which PEV already has in
    state — rather than SafetyGuard's ``_safety_modified_files``, because that is
    only snapshotted in ``after_agent`` (after the loop) and is empty mid-Verify.
    """
    files: list[str] = []
    seen: set[str] = set()
    for msg in state.get("messages", []):
        if getattr(msg, "type", None) != "ai":
            continue
        for tool_call in getattr(msg, "tool_calls", None) or []:
            name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
            if name not in _SAFETY_FILE_TOOLS:
                continue
            args = tool_call.get("args") if isinstance(tool_call, dict) else getattr(tool_call, "args", None)
            path = args.get("path") if isinstance(args, dict) else None
            if path and path not in seen:
                seen.add(path)
                files.append(path)
    return files


class PEVState(AgentState):
    """Agent state extended with PEV phase-tracking fields."""

    _pev_phase: Annotated[NotRequired[str], PrivateStateAttr]
    """Current PEV phase: ``"trivial"``, ``"fast"``, ``"plan"``, ``"execute"``, or ``"verify"``."""

    _pev_iteration: Annotated[NotRequired[int], PrivateStateAttr]
    """Number of model calls made in the current phase (reset on phase transitions)."""

    _pev_error_count: Annotated[NotRequired[int], PrivateStateAttr]
    """Number of ``ISSUES FOUND`` signals received (circuit-breaker counter)."""

    _pev_plan: Annotated[NotRequired[str | None], PrivateStateAttr]
    """Plan text captured at the end of the plan phase."""

    _pev_recent_hashes: Annotated[NotRequired[list[str]], PrivateStateAttr]
    """SHA-256 hashes of the last ``_HASH_WINDOW`` AI responses for loop detection."""

    _pev_task: Annotated[NotRequired[str | None], PrivateStateAttr]
    """Original user task text, captured in ``before_agent``."""


class PEVStateUpdate(TypedDict, total=False):
    """Partial state update returned by PEV hooks.

    All fields are optional — only include what changed.
    """

    _pev_phase: str
    _pev_iteration: int
    _pev_error_count: int
    _pev_plan: str | None
    _pev_recent_hashes: list[str]
    _pev_task: str | None


class PEVMiddleware(AgentMiddleware):
    """Middleware that wraps task execution in a Plan → Execute → Verify loop.

    Complex tasks go through three phases:

    - **plan**: the model produces a numbered plan and emits ``PLAN COMPLETE``.
    - **execute**: the model executes the plan and emits ``EXECUTION COMPLETE``.
    - **verify**: the model inspects results with read-only tools and emits
      ``VERIFIED OK`` or ``ISSUES FOUND``.

    Simpler tasks use ``fast`` (single-shot) or ``trivial`` (pass-through) mode.

    Circuit breaker terminates when error count ≥ ``_MAX_ERRORS`` or the
    per-phase iteration count exceeds its cap (``_MAX_ITERATIONS`` for
    plan/verify, ``_MAX_EXECUTE_ITERATIONS`` for execute; the count resets on
    each phase transition).  Loop detection terminates when the same response
    hash appears ≥ ``_LOOP_THRESHOLD`` times in the last ``_HASH_WINDOW`` turns.
    """

    state_schema = PEVState

    def __init__(self, diagnostics_provider: DiagnosticsProvider | None = None) -> None:
        """Initialise PEV middleware.

        Args:
            diagnostics_provider: Optional async callable invoked during the
                Verify phase with the list of files edited this task.  If it
                returns a non-empty string, that text is appended to the verify
                prompt so the model sees concrete errors and self-corrects.
                When ``None`` (the default), Verify behaves exactly as before —
                LSP is purely additive and never a precondition.
        """
        super().__init__()
        self._diagnostics_provider = diagnostics_provider

    def before_agent(self, state: PEVState, runtime: Runtime) -> dict[str, Any] | None:
        """Classify the task and initialise PEV state before the first model call.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update that sets the initial phase and zeroes all counters.
        """
        messages = state.get("messages", [])
        task = ""
        for msg in reversed(messages):
            if getattr(msg, "type", None) == "human":
                raw = msg.content
                task = raw if isinstance(raw, str) else str(raw)
                break

        phase = _classifier.get_initial_phase(task)
        return {
            "_pev_phase": phase,
            "_pev_iteration": 0,
            "_pev_error_count": 0,
            "_pev_plan": None,
            "_pev_recent_hashes": [],
            "_pev_task": task,
        }

    async def abefore_agent(self, state: PEVState, runtime: Runtime) -> dict[str, Any] | None:
        """Delegate asynchronously to :meth:`before_agent`.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update that sets the initial phase and zeroes all counters.
        """
        return self.before_agent(state, runtime)

    def _build_modified_request(self, request: ModelRequest) -> ModelRequest:
        """Inject the phase prompt and restrict tools per phase.

        Args:
            request: Incoming model request.

        Returns:
            Modified model request with phase prompt appended and, in the
            ``plan`` phase, no tools bound; in the ``verify`` phase, tools
            filtered to the read-only set.
        """
        phase: str = request.state.get("_pev_phase", "fast")
        phase_prompt = _PHASE_PROMPTS.get(phase)

        new_system_message = request.system_message
        if phase_prompt:
            new_system_message = append_to_system_message(request.system_message, phase_prompt)

        if phase == "plan" and request.tools:
            # Plan phase binds NO tools: with tools available the model acts
            # instead of planning and never emits PLAN COMPLETE. Execution
            # gets the full toolset back after the marker.
            return request.override(system_message=new_system_message, tools=[])

        if phase == "verify" and request.tools:
            filtered = [t for t in request.tools if getattr(t, "name", None) in _VERIFY_READONLY_TOOLS]
            return request.override(system_message=new_system_message, tools=filtered)

        return request.override(system_message=new_system_message)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject phase context into every synchronous model call.

        Args:
            request: Model request from the agent loop.
            handler: Next handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        return handler(self._build_modified_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Inject phase context into every asynchronous model call.

        In the Verify phase, additionally run the optional diagnostics provider
        on the files edited this task and append any ERRORS to the verify prompt
        (W3.3).  This only augments the prompt — the model still authors
        ``VERIFIED OK`` / ``ISSUES FOUND``, so markers and circuit breakers are
        unchanged.  With no provider, this is identical to the original path.

        Args:
            request: Model request from the agent loop.
            handler: Next async handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        modified = self._build_modified_request(request)
        if (
            self._diagnostics_provider is not None
            and request.state.get("_pev_phase") == "verify"
        ):
            modified = await self._maybe_inject_diagnostics(modified, request)
        return await handler(modified)

    async def _maybe_inject_diagnostics(
        self, modified: ModelRequest, request: ModelRequest
    ) -> ModelRequest:
        """Append language-server ERRORS to the verify prompt, if any.

        Best-effort: any failure (or no provider result) leaves the request
        untouched, so Verify degrades to its standard behaviour.
        """
        try:
            files = _collect_modified_files(request.state)
            if not files:
                return modified
            addendum = await self._diagnostics_provider(files)  # type: ignore[misc]
            if not addendum:
                return modified
            new_system_message = append_to_system_message(modified.system_message, addendum)
            return modified.override(system_message=new_system_message)
        except Exception:  # noqa: BLE001 — verify must never break on diagnostics
            logger.debug("LSP verify diagnostics injection skipped", exc_info=True)
            return modified

    def _compute_next_state(self, state: PEVState) -> dict[str, Any]:
        """Derive the next state update from the latest model response.

        Detects phase-completion markers in the last AI message, applies
        circuit-breaker and loop-detection guards, and returns a partial
        state-update dict that may include a ``jump_to`` key.

        Args:
            state: Agent state after the most recent model call.

        Returns:
            Dict of state field updates, possibly with a ``jump_to`` key
            set to ``"end"`` or ``"model"``.
        """
        phase: str = state.get("_pev_phase", "fast")
        if phase in ("trivial", "fast"):
            return {}

        iteration: int = state.get("_pev_iteration", 0)
        error_count: int = state.get("_pev_error_count", 0)
        recent_hashes: list[str] = list(state.get("_pev_recent_hashes", []))

        last_content = ""
        last_ai_message = None
        for msg in reversed(state.get("messages", [])):
            if getattr(msg, "type", None) == "ai":
                last_ai_message = msg
                raw = msg.content
                last_content = raw if isinstance(raw, str) else str(raw)
                break

        # Tool-call-only turns have empty content; hashing them would make any
        # three consecutive tool rounds look like a loop. Only prose feeds the
        # detector.
        loop_detected = False
        if last_content.strip():
            content_hash = hashlib.sha256(last_content.encode()).hexdigest()
            recent_hashes = (recent_hashes + [content_hash])[-_HASH_WINDOW:]
            loop_detected = recent_hashes.count(content_hash) >= _LOOP_THRESHOLD

        new_iteration = iteration + 1
        max_iterations = _MAX_EXECUTE_ITERATIONS if phase == "execute" else _MAX_ITERATIONS
        if loop_detected or error_count >= _MAX_ERRORS or new_iteration > max_iterations:
            breaker_update: dict[str, Any] = {
                "_pev_iteration": new_iteration,
                "_pev_recent_hashes": recent_hashes,
                "jump_to": "end",
            }
            # Honest status: if the final turn carries no phase marker, the UI
            # would render nothing. Append a negative verdict naming the dead
            # phase — never a fabricated VERIFIED OK.
            if not any(marker in last_content.upper() for marker in _PHASE_MARKERS):
                breaker_update["messages"] = [
                    AIMessage(
                        content=f"ISSUES FOUND: {phase} phase did not complete "
                        "(stopped by iteration cap or circuit breaker)."
                    )
                ]
            return breaker_update

        upper = last_content.upper()
        update: dict[str, Any] = {
            "_pev_iteration": new_iteration,
            "_pev_recent_hashes": recent_hashes,
        }

        # The iteration cap is per phase: each transition starts a fresh count.
        if phase == "plan" and "PLAN COMPLETE" in upper:
            update["_pev_phase"] = "execute"
            update["_pev_iteration"] = 0
            update["_pev_plan"] = last_content
            update["jump_to"] = "model"
        elif phase == "plan" and not getattr(last_ai_message, "tool_calls", None):
            # No marker and no tool calls: the model->tools edge would end the
            # run silently. Retry the plan; the per-phase iteration cap above
            # bounds the retries.
            update["jump_to"] = "model"
        elif phase == "execute" and "EXECUTION COMPLETE" in upper:
            update["_pev_phase"] = "verify"
            update["_pev_iteration"] = 0
            update["jump_to"] = "model"
        elif phase == "execute" and not getattr(last_ai_message, "tool_calls", None):
            # No marker and no tool calls (e.g. an empty reasoning-only turn):
            # the model->tools edge would end the run silently. Retry, bounded
            # by the per-phase iteration cap above — same gap as plan.
            update["jump_to"] = "model"
        elif phase == "verify":
            if "VERIFIED OK" in upper:
                update["jump_to"] = "end"
            elif "ISSUES FOUND" in upper:
                update["_pev_phase"] = "execute"
                update["_pev_iteration"] = 0
                update["_pev_error_count"] = error_count + 1
                update["jump_to"] = "model"
            elif not getattr(last_ai_message, "tool_calls", None):
                # No verdict and no tool calls: re-prompt for a verdict
                # instead of ending silently; the iteration cap bounds it.
                update["jump_to"] = "model"

        return update

    @hook_config(can_jump_to=["end", "model"])
    def after_model(self, state: PEVState, runtime: Runtime) -> dict[str, Any] | None:
        """Advance the PEV phase after each synchronous model response.

        Args:
            state: Agent state after the model call.
            runtime: Runtime context.

        Returns:
            State update with optional ``jump_to``, or ``None`` to continue normally.
        """
        result = self._compute_next_state(state)
        return result if result else None

    @hook_config(can_jump_to=["end", "model"])
    async def aafter_model(self, state: PEVState, runtime: Runtime) -> dict[str, Any] | None:
        """Advance the PEV phase after each asynchronous model response.

        Args:
            state: Agent state after the model call.
            runtime: Runtime context.

        Returns:
            State update with optional ``jump_to``, or ``None`` to continue normally.
        """
        return self.after_model(state, runtime)


__all__ = ["PEVMiddleware", "PEVState", "PEVStateUpdate"]
