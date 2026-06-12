"""Workflow middleware — executes structured multi-step workflows from Markdown files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr, hook_config

from deepagents.middleware._utils import append_to_system_message

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.runtime import Runtime

_WORKFLOW_TRIGGER_PREFIXES: tuple[str, ...] = ("run workflow ", "/workflow ")
_TURBO_ALL_TAG: str = "// turbo-all"
_TURBO_TAG: str = "// turbo"
_STEP_PATTERN: re.Pattern[str] = re.compile(r"^\d+\.\s+(.+)$")


class WorkflowLoader:
    """Parses HCode workflow Markdown files into ordered step lists.

    Workflow format::

        # Workflow: Name

        ## Steps
        1. First step
        2. Second step // turbo
        3. Third step // turbo-all
        4. Fourth step

    Annotations:

    - ``// turbo`` — execute this step without asking for confirmation.
    - ``// turbo-all`` — this step **and all subsequent steps** run without
      confirmation.
    """

    def load(self, path: str) -> list[dict[str, Any]]:
        """Parse a workflow Markdown file and return its step list.

        Args:
            path: Absolute or relative path to the ``.md`` workflow file.

        Returns:
            List of ``{"text": str, "turbo": bool}`` dicts in step order.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
        """
        content = Path(path).read_text()
        steps: list[dict[str, Any]] = []
        turbo_all = False

        for line in content.splitlines():
            m = _STEP_PATTERN.match(line.strip())
            if not m:
                continue

            text: str = m.group(1)
            turbo: bool = turbo_all

            if _TURBO_ALL_TAG in text:
                text = text.replace(_TURBO_ALL_TAG, "").strip()
                turbo = True
                turbo_all = True
            elif _TURBO_TAG in text:
                text = text.replace(_TURBO_TAG, "").strip()
                turbo = True

            steps.append({"text": text, "turbo": turbo})

        return steps


class WorkflowState(AgentState):
    """Agent state extended with workflow execution fields."""

    _workflow_active: Annotated[NotRequired[bool], PrivateStateAttr]
    """``True`` while a workflow is being executed."""

    _workflow_name: Annotated[NotRequired[str | None], PrivateStateAttr]
    """Name of the currently active workflow."""

    _workflow_steps: Annotated[NotRequired[list[dict[str, Any]]], PrivateStateAttr]
    """Ordered list of ``{"text": str, "turbo": bool}`` step dicts."""

    _workflow_current_step: Annotated[NotRequired[int], PrivateStateAttr]
    """Zero-based index of the step currently being executed."""

    _workflow_results: Annotated[NotRequired[list[str]], PrivateStateAttr]
    """Accumulated result summaries from completed steps."""


class WorkflowMiddleware(AgentMiddleware):
    """Middleware that runs HCode structured workflows step by step.

    A workflow is triggered when the user's message starts with
    ``"run workflow <name>"`` or ``"/workflow <name>"``.  The corresponding
    ``.md`` file is loaded from ``workflows_dir``, parsed into steps, and
    executed one step per model call.

    On each call, the current step is injected into the system prompt via
    ``wrap_model_call``.  After each model response, ``after_model`` advances
    the step counter and, when all steps are complete, jumps to ``"end"``.

    ``// turbo`` steps include an instruction to execute without requesting
    confirmation from the user.

    Args:
        workflows_dir: Directory containing workflow ``.md`` files.
            Defaults to ``.hcode/workflows``.
        loader: ``WorkflowLoader`` instance for parsing files.
            Defaults to a fresh :class:`WorkflowLoader`.
    """

    state_schema = WorkflowState

    def __init__(
        self,
        workflows_dir: str = ".hcode/workflows",
        loader: WorkflowLoader | None = None,
    ) -> None:
        """Initialise with the workflow directory and an optional custom loader.

        Args:
            workflows_dir: Local path to the directory containing workflow files.
            loader: Custom workflow parser, or ``None`` to use the default.
        """
        self.workflows_dir: Path = Path(workflows_dir)
        self._loader: WorkflowLoader = loader or WorkflowLoader()

    def _detect_workflow_name(self, task: str) -> str | None:
        """Extract the workflow name from a trigger task string.

        Args:
            task: First human message content.

        Returns:
            Workflow name (stripped), or ``None`` if the task is not a workflow trigger.
        """
        lower = task.lower().strip()
        for prefix in _WORKFLOW_TRIGGER_PREFIXES:
            if lower.startswith(prefix):
                return task[len(prefix):].strip()
        return None

    def before_agent(self, state: WorkflowState, runtime: Runtime) -> dict[str, Any] | None:
        """Detect a workflow trigger and initialise workflow state.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with workflow configuration, or inactive state for
            non-workflow tasks.
        """
        messages = state.get("messages", [])
        task = ""
        for msg in reversed(messages):
            if getattr(msg, "type", None) == "human":
                raw = msg.content
                task = raw if isinstance(raw, str) else str(raw)
                break

        workflow_name = self._detect_workflow_name(task)
        if not workflow_name:
            return {
                "_workflow_active": False,
                "_workflow_name": None,
                "_workflow_steps": [],
                "_workflow_current_step": 0,
                "_workflow_results": [],
            }

        workflow_path = self.workflows_dir / f"{workflow_name}.md"
        try:
            steps = self._loader.load(str(workflow_path))
        except FileNotFoundError:
            return {
                "_workflow_active": False,
                "_workflow_name": workflow_name,
                "_workflow_steps": [],
                "_workflow_current_step": 0,
                "_workflow_results": [],
            }

        return {
            "_workflow_active": True,
            "_workflow_name": workflow_name,
            "_workflow_steps": steps,
            "_workflow_current_step": 0,
            "_workflow_results": [],
        }

    async def abefore_agent(self, state: WorkflowState, runtime: Runtime) -> dict[str, Any] | None:
        """Delegate asynchronously to :meth:`before_agent`.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with workflow configuration.
        """
        return self.before_agent(state, runtime)

    def _build_step_prompt(self, steps: list[dict[str, Any]], current: int) -> str:
        """Build the system-prompt fragment for the current workflow step.

        Args:
            steps: Full ordered list of workflow steps.
            current: Zero-based index of the step to render.

        Returns:
            Formatted Markdown string describing the current step.
        """
        step = steps[current]
        total = len(steps)
        prompt = f"## Workflow Execution\n\nStep {current + 1}/{total}: {step['text']}"
        if step.get("turbo"):
            prompt += "\n\nExecute this step directly without asking for confirmation."
        return prompt

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject the current workflow step into the system prompt.

        Args:
            request: Model request from the agent loop.
            handler: Next handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        if not request.state.get("_workflow_active", False):
            return handler(request)

        steps: list[dict[str, Any]] = request.state.get("_workflow_steps", [])
        current: int = request.state.get("_workflow_current_step", 0)
        if not steps or current >= len(steps):
            return handler(request)

        new_sm = append_to_system_message(request.system_message, self._build_step_prompt(steps, current))
        return handler(request.override(system_message=new_sm))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Inject the current workflow step into the system prompt (async).

        Args:
            request: Model request from the agent loop.
            handler: Next async handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        if not request.state.get("_workflow_active", False):
            return await handler(request)

        steps: list[dict[str, Any]] = request.state.get("_workflow_steps", [])
        current: int = request.state.get("_workflow_current_step", 0)
        if not steps or current >= len(steps):
            return await handler(request)

        new_sm = append_to_system_message(request.system_message, self._build_step_prompt(steps, current))
        return await handler(request.override(system_message=new_sm))

    @hook_config(can_jump_to=["end", "model"])
    def after_model(self, state: WorkflowState, runtime: Runtime) -> dict[str, Any] | None:
        """Advance the workflow step counter after each model response.

        When all steps are complete the agent is directed to ``"end"``.
        Otherwise it loops back to ``"model"`` to execute the next step.

        Args:
            state: Agent state after the model call.
            runtime: Runtime context.

        Returns:
            State update with incremented step counter and a ``jump_to`` key,
            or ``None`` when no workflow is active.
        """
        if not state.get("_workflow_active", False):
            return None

        steps: list[dict[str, Any]] = state.get("_workflow_steps", [])
        current: int = state.get("_workflow_current_step", 0)
        next_step = current + 1

        if next_step >= len(steps):
            return {
                "_workflow_active": False,
                "_workflow_current_step": next_step,
                "jump_to": "end",
            }

        return {
            "_workflow_current_step": next_step,
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["end", "model"])
    async def aafter_model(self, state: WorkflowState, runtime: Runtime) -> dict[str, Any] | None:
        """Delegate asynchronously to :meth:`after_model`.

        Args:
            state: Agent state after the model call.
            runtime: Runtime context.

        Returns:
            State update with incremented step counter and a ``jump_to`` key.
        """
        return self.after_model(state, runtime)


__all__ = ["WorkflowLoader", "WorkflowMiddleware", "WorkflowState"]
