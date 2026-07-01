"""Human-in-the-loop plan review — pause at the plan→execute boundary.

After PEV finishes the PLAN phase it flips ``_pev_phase`` to ``"execute"`` and
jumps back to the model loop (``jump_to="model"``). That jump re-enters the
``before_model`` hooks, so a ``before_model`` that fires the FIRST time it sees
``_pev_phase == "execute"`` sits exactly on the plan→execute boundary — before
any execute-phase model call or tool runs.

``PlanReviewMiddleware`` uses that boundary to ``interrupt()`` for a human
decision:

* **accept** → mark the plan reviewed and continue into EXECUTE unchanged.
* **reject** → mark reviewed, drop a short message, and ``jump_to="end"`` so the
  run stops cleanly before any code is written.

Why this is safe / additive:
  - It NEVER edits vendored ``pev.py`` — it only READS PEV's public phase
    contract (``_pev_phase`` / ``_pev_plan``), the same wrap-don't-edit pattern as
    ``SelectiveSkillsMiddleware`` and ``ResilientChatModel``.
  - It is only added to the agent when ``plan_review=True`` (see the factory), so
    a default run is byte-identical to before — zero regression.
  - ``_plan_reviewed`` is a private state flag so the interrupt fires ONCE per
    plan; the many later execute-phase model calls sail through untouched.

Edit-the-plan is intentionally NOT implemented here (accept/reject only) — it is a
separate fast-follow because rewriting the plan means rewriting message history
against PEV's bookkeeping.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    PrivateStateAttr,
    hook_config,
)
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

logger = logging.getLogger(__name__)

# The interrupt payload's ``type`` — the daemon matches on this to tell a plan
# review apart from any other interrupt and to emit the ``plan_review`` event.
PLAN_REVIEW_INTERRUPT = "plan_review"

# Message recorded when the user rejects a plan, so the transcript has an honest
# terminal line instead of an empty end.
_REJECTED_MESSAGE = "Plan rejected by the user — execution was skipped."


class PlanReviewState(AgentState):
    """Agent state + a one-shot flag marking the plan as already reviewed."""

    _plan_reviewed: Annotated[NotRequired[bool], PrivateStateAttr]


class PlanReviewMiddleware(AgentMiddleware):
    """Pause for human accept/reject at the plan→execute boundary.

    Wraps PEV without editing it. Only active when added to the agent
    (``plan_review=True``); otherwise absent entirely = no interrupt = unchanged.
    """

    state_schema = PlanReviewState

    def _maybe_review(self, state: dict) -> dict[str, Any] | None:
        """Interrupt once, at the first execute-phase entry; map the decision.

        Returns a state update (or ``None`` to continue). On the plan phase and on
        every execute call after the review, returns ``None`` so behaviour is
        unchanged; only the single plan→execute transition interrupts.
        """
        # Only the plan→execute boundary, and only once.
        if state.get("_pev_phase") != "execute":
            return None
        if state.get("_plan_reviewed"):
            return None

        plan = state.get("_pev_plan") or _fallback_plan(state)
        # interrupt() raises to pause the graph; on resume it RETURNS the value the
        # daemon supplied via Command(resume=...). Same primitive HITL shell
        # approval uses. The daemon sends {"accept": bool}.
        decision = interrupt({"type": PLAN_REVIEW_INTERRUPT, "plan": plan})
        accept = decision.get("accept", False) if isinstance(decision, dict) else bool(decision)

        if accept:
            # Continue into EXECUTE exactly as an un-reviewed run would.
            return {"_plan_reviewed": True}

        # Reject: stop cleanly BEFORE any execute model call / tool. jump_to="end"
        # is honoured because before_model declares can_jump_to=["end"] below.
        return {
            "_plan_reviewed": True,
            "jump_to": "end",
            "messages": [AIMessage(content=_REJECTED_MESSAGE)],
        }

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: PlanReviewState, runtime: Runtime) -> dict[str, Any] | None:
        return self._maybe_review(state)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: PlanReviewState, runtime: Runtime) -> dict[str, Any] | None:
        # The daemon drives the agent via astream_events (async), so this is the
        # hot path. Identical logic to the sync hook — interrupt() is transport
        # agnostic.
        return self._maybe_review(state)


def _fallback_plan(state: dict) -> str:
    """Best-effort plan text if ``_pev_plan`` is somehow unset — the last AI message."""
    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "type", None) == "ai":
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return "(plan unavailable)"


__all__ = ["PlanReviewMiddleware", "PlanReviewState", "PLAN_REVIEW_INTERRUPT"]
