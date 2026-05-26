"""PEV (Plan-Execute-Verify) middleware for structured task execution."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr, hook_config
from typing_extensions import TypedDict

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.task_classifier import TaskClassifier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.runtime import Runtime

_PLAN_PROMPT: str = """\
## PEV Planning Phase

Produce a clear, numbered plan covering every step required to complete the task.
Be specific: name files, tools, and values you intend to use.

When your plan is ready, output this marker alone on its own line:
PLAN COMPLETE"""

_EXECUTE_PROMPT: str = """\
## PEV Execution Phase

Execute the plan from the previous step one step at a time.
Use available tools to accomplish each step.

When all steps are complete and results are correct, output this marker alone on its own line:
EXECUTION COMPLETE"""

_VERIFY_PROMPT: str = """\
## PEV Verification Phase

Inspect the execution results.  Do not modify any files during this phase.
Confirm that every planned step completed, outputs are correct, and no errors remain.

If everything is correct, output this marker alone on its own line:
VERIFIED OK

If problems require re-execution, output:
ISSUES FOUND: <brief description>"""

_FAST_PROMPT: str = """\
## PEV Fast Mode

Complete the task in a single response using the available tools."""

_PHASE_PROMPTS: dict[str, str] = {
    "plan": _PLAN_PROMPT,
    "execute": _EXECUTE_PROMPT,
    "verify": _VERIFY_PROMPT,
    "fast": _FAST_PROMPT,
}

_VERIFY_READONLY_TOOLS: frozenset[str] = frozenset({"read_file", "ls", "glob", "grep"})
_MAX_ERRORS: int = 3
_MAX_ITERATIONS: int = 5
_HASH_WINDOW: int = 5
_LOOP_THRESHOLD: int = 3

_classifier: TaskClassifier = TaskClassifier()


class PEVState(AgentState):
    """Agent state extended with PEV phase-tracking fields."""

    _pev_phase: Annotated[NotRequired[str], PrivateStateAttr]
    """Current PEV phase: ``"trivial"``, ``"fast"``, ``"plan"``, ``"execute"``, or ``"verify"``."""

    _pev_iteration: Annotated[NotRequired[int], PrivateStateAttr]
    """Number of model calls made in the current session."""

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

    Circuit breaker terminates when error count ≥ ``_MAX_ERRORS`` or iteration
    count > ``_MAX_ITERATIONS``.  Loop detection terminates when the same
    response hash appears ≥ ``_LOOP_THRESHOLD`` times in the last ``_HASH_WINDOW``
    turns.
    """

    state_schema = PEVState

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
        for msg in messages:
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
        """Inject the phase prompt and, for verify, restrict tools to read-only.

        Args:
            request: Incoming model request.

        Returns:
            Modified model request with phase prompt appended and, in the
            ``verify`` phase, tools filtered to the read-only set.
        """
        phase: str = request.state.get("_pev_phase", "fast")
        phase_prompt = _PHASE_PROMPTS.get(phase)

        new_system_message = request.system_message
        if phase_prompt:
            new_system_message = append_to_system_message(request.system_message, phase_prompt)

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

        Args:
            request: Model request from the agent loop.
            handler: Next async handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        return await handler(self._build_modified_request(request))

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
        for msg in reversed(state.get("messages", [])):
            if getattr(msg, "type", None) == "ai":
                raw = msg.content
                last_content = raw if isinstance(raw, str) else str(raw)
                break

        content_hash = hashlib.sha256(last_content.encode()).hexdigest()
        recent_hashes = (recent_hashes + [content_hash])[-_HASH_WINDOW:]
        loop_detected = recent_hashes.count(content_hash) >= _LOOP_THRESHOLD

        new_iteration = iteration + 1
        if loop_detected or error_count >= _MAX_ERRORS or new_iteration > _MAX_ITERATIONS:
            return {
                "_pev_iteration": new_iteration,
                "_pev_recent_hashes": recent_hashes,
                "jump_to": "end",
            }

        upper = last_content.upper()
        update: dict[str, Any] = {
            "_pev_iteration": new_iteration,
            "_pev_recent_hashes": recent_hashes,
        }

        if phase == "plan" and "PLAN COMPLETE" in upper:
            update["_pev_phase"] = "execute"
            update["_pev_plan"] = last_content
            update["jump_to"] = "model"
        elif phase == "execute" and "EXECUTION COMPLETE" in upper:
            update["_pev_phase"] = "verify"
            update["jump_to"] = "model"
        elif phase == "verify":
            if "VERIFIED OK" in upper:
                update["jump_to"] = "end"
            elif "ISSUES FOUND" in upper:
                update["_pev_phase"] = "execute"
                update["_pev_error_count"] = error_count + 1
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
