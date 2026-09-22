"""Plan-phase tool shim — keep the PLAN phase legal on providers that reject
a request carrying no tools.

## The failure this fixes (measured, not hypothetical)

PEV's plan phase deliberately binds NO tools (vendored ``pev.py``: *"Plan phase
binds NO tools: with tools available the model acts instead of planning"*).
LangChain then takes its no-tools branch and calls ``model.bind(...)`` WITHOUT
sending a ``tools`` field at all (``langchain/agents/factory.py``: ``if
final_tools: ... bind_tools(...)`` / ``return request.model.bind(...)``).

Groq interprets a request with no tools as ``tool_choice="none"``. The
``gpt-oss`` family emits a tool call anyway — it is trained to plan through its
tool channel — and the provider rejects the whole request:

    Tool choice is none, but model called a tool

The result is fatal, not degraded: **every** planning-mode run dies at the first
model call in ~5 s having produced zero tokens. Measured in
``eval/results.jsonl``: 4 of 4 planning-mode runs that reached the API failed
this way, versus 0 of 3 fast-mode runs (which bind the full toolset and are
therefore unaffected).

This is NOT a PEV logic fault. The keyless ``planning-full-arc`` probe
(``scripts/probe_daemon.py``) passes against a scripted model, because a
scripted model does not emit a stray tool call. The defect only appears with a
real ``gpt-oss`` model on a provider that enforces the ``tool_choice=none``
contract — which is precisely the enterprise target, so it cannot be dismissed
as a free-tier artefact.

## The fix

Bind exactly ONE inert tool when the phase would otherwise bind zero, so the
request carries a non-empty ``tools`` array and the provider never infers
``tool_choice="none"``. The model may now legally emit a tool call during
planning; if it does, the tool is a no-op whose result simply redirects it back
to producing the plan.

Why a shim rather than editing ``pev.py``: the vendored file stays untouched
(Rule 14 / the project's wrap-don't-edit norm), so the coordination surface is
``factory.py`` alone rather than shared middleware.

## Why this does not leak into the other phases

``planning_note`` must be REGISTERED with the agent to be executable at all —
LangChain raises *"Middleware added tools that the agent doesn't know how to
execute"* for a tool injected into ``request.tools`` that was never registered.
Registration happens through this middleware's ``tools`` attribute, which would
otherwise make the tool visible in EVERY phase. So the shim does both halves:

* tool list empty  -> inject ``planning_note`` (the plan phase);
* tool list non-empty -> STRIP ``planning_note`` from it (fast / execute /
  verify), so the normal tool menu is byte-identical to today.

Registered LAST in the factory so it observes the final, fully-filtered tool
list — after ``_ToolExclusionMiddleware`` and prompt slimming have had their
say — and can never be undone by a later middleware.

Kill-switch: ``HCODE_PLAN_TOOL_SHIM=0``/``false``/``off`` omits it entirely.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

SHIM_TOOL_NAME = "planning_note"


@tool
def planning_note(note: str) -> str:
    """Jot a one-line note while planning. Planning is a thinking-only phase:
    prefer writing the plan directly in your reply instead of calling this.

    Args:
        note: A short note about the plan.
    """
    # Inert by design. Its only jobs are (a) to make the bound tool list
    # non-empty so the provider does not infer tool_choice="none", and (b) if
    # the model does call it, to hand back a result that steers straight back to
    # producing the plan rather than leaving the turn empty.
    return (
        "Noted. No action was taken — planning is a thinking-only phase. "
        "Write the full numbered plan in your reply now, naming the exact "
        "file(s) and tool(s) each step will use, and end with the line "
        "PLAN COMPLETE."
    )


class PlanPhaseToolShimMiddleware(AgentMiddleware):
    """Guarantee a non-empty bound tool list, so a no-tools phase stays legal.

    Absent (kill-switch off) the agent behaves exactly as before. Present, the
    ONLY request it changes is one whose tool list is empty — in every other
    phase it removes its own tool and hands the request through untouched.
    """

    # Registering the tool here is what makes it executable by the ToolNode.
    tools = [planning_note]

    def _shim(self, request: ModelRequest) -> ModelRequest:
        current = list(request.tools or [])

        if not current:
            # The plan phase (or any phase that stripped every tool). Bind the
            # single inert tool so the provider sees a real tools array.
            logger.debug(
                "[plan-tool-shim] binding %s (phase=%s had zero tools)",
                SHIM_TOOL_NAME, (request.state or {}).get("_pev_phase"),
            )
            return request.override(tools=[planning_note])

        # Every other phase: make sure our shim tool is NOT part of the normal
        # menu, so the model's choices are exactly what they were before.
        filtered = [t for t in current if getattr(t, "name", None) != SHIM_TOOL_NAME]
        if len(filtered) == len(current):
            return request
        return request.override(tools=filtered)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(self._shim(request))

    async def awrap_model_call(
        self, request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        return await handler(self._shim(request))


__all__ = ["PlanPhaseToolShimMiddleware", "planning_note", "SHIM_TOOL_NAME"]
