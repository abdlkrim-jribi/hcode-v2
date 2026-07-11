"""Force the full PEV arc for a task, regardless of the classifier's verdict.

PEV's ``TaskClassifier`` (vendored) routes any task WITHOUT a complex-engineering
keyword (implement/refactor/build/…) to the ``fast`` phase, where
``_compute_next_state`` returns ``{}`` and PEV never transitions — so an ordinary
task ("add a comment to hello.py") never reaches Execute or Verify, and the
Verify-phase LSP self-correction never fires. That is correct as a default (a
one-line edit shouldn't pay for plan + verify round-trips), but it means the
composer's "Plan" button had no way to opt a task INTO the full arc.

``ForcePlanMiddleware`` is that opt-in. Its ``before_agent`` sets
``_pev_phase = "plan"``, overriding whatever phase PEV's classifier chose. It is
registered AFTER ``PEVMiddleware`` (see the factory), and the agent framework
runs ``before_agent`` hooks in registration order, merging each update into state
before the next — so this later write wins. A forced run therefore always plans
first, then Execute, then Verify (+ LSP on edited files), exactly what the "Plan"
mode promises.

Why this is safe / additive:
  - It NEVER edits vendored ``pev.py`` / ``task_classifier.py`` — it only WRITES
    PEV's public ``_pev_phase`` channel, the same wrap-don't-edit pattern as
    ``PlanReviewMiddleware`` and ``SelectiveSkillsMiddleware``.
  - It is only added to the agent when ``force_plan=True`` (see the factory), so
    a default run (mode="fast" or absent) is byte-identical to before — the
    classifier's verdict stands and zero behaviour changes.

Composition with plan review: when ``plan_review`` is also on, both middlewares'
``before_agent`` hooks set ``_pev_phase = "plan"`` — an idempotent write of the
same value, so they compose cleanly in any registration order. (Once plan review
itself forces the plan phase, this middleware is redundant with it but still
harmless; kept independent so it works on its own and regardless of merge order.)

Known residual (shared with plan review): a model that never emits the
``PLAN COMPLETE`` / ``EXECUTION COMPLETE`` phase markers ends via PEV's honest
iteration-cap breaker instead of advancing — the run degrades visibly, it does
not silently skip the arc.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.runtime import Runtime


class ForcePlanMiddleware(AgentMiddleware):
    """Pin the initial PEV phase to ``plan`` so the full arc runs for any task.

    Only active when added to the agent (``force_plan=True``); otherwise absent
    entirely = the classifier's phase stands = unchanged behaviour.
    """

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Override the classifier's initial phase with ``plan``.

        Merges over ``PEVMiddleware.before_agent``'s update because this
        middleware is registered after it and ``before_agent`` hooks run in
        registration order (later write wins). Writes only PEV's ``_pev_phase``
        channel — no new state keys.
        """
        return {"_pev_phase": "plan"}

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        # The daemon drives the agent via astream_events (async) — same logic.
        return self.before_agent(state, runtime)


__all__ = ["ForcePlanMiddleware"]
