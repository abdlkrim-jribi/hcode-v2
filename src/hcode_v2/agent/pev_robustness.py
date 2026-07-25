"""PEV robustness — keep the arc moving when the model misbehaves.

The provider bake-off (M5, gpt-oss-120b on free tiers) surfaced two failure
modes that stall a DAEMON run — where, unlike the CLI, there is no human to
recover the situation:

1. **The model asks the user a clarifying question and stops.** ``ask_user`` /
   ``confirm`` are already excluded in non-interactive runs (see the factory's
   T3 note), so the model asks in PROSE instead: a turn that ends with a
   question, no tool calls, no phase marker. In PEV's ``fast`` phase that turn
   has no tool calls, so the agent loop exits (``model_to_tools`` sees zero tool
   calls → END) — the "answer" the user gets is a question they cannot answer.
   In ``plan``/``execute``/``verify`` PEV re-prompts blindly up to the per-phase
   iteration cap, burning round-trips (expensive, and effectively a long stall
   on a rate-limited free tier).

2. **The model claims ``EXECUTION COMPLETE`` having edited nothing.** PEV's
   ``_compute_next_state`` advances ``execute → verify`` on the marker alone; it
   never checks that any file was actually written. Verify then "passes"
   vacuously over an unchanged tree — a silent success claim for work not done.

``PEVRobustnessMiddleware`` closes both, plus a guarded bonus (marker
tolerance), WITHOUT editing vendored ``pev.py``. It exploits the same routing
fact ``PlanReviewMiddleware`` relies on: middleware ``after_model`` hooks run in
REVERSE registration order (``langchain.agents.factory`` chains them from the
last-registered down to the first), so a middleware registered AFTER PEV runs
its ``after_model`` BEFORE PEV's — and if it sets ``jump_to`` the conditional
edge jumps immediately, so PEV's ``after_model`` never runs for that turn. This
middleware uses that to PRE-EMPT PEV's transition for the misbehaving cases and
otherwise return ``None`` to defer to PEV unchanged. It writes only the standard
``jump_to`` + ``messages`` channels (never PEV's ``_pev_phase``) — the same
wrap-don't-edit contract as plan-review's reject path.

## The three interventions (each a NUDGE, never a fabricated transition)

Every intervention does the SAME safe thing: append a short corrective message
and ``jump_to="model"`` so the model gets another turn WITH guidance, instead of
the run dying (fast) or spinning (plan/execute/verify). None of them writes a
PEV phase or synthesizes a marker — the model still authors its own markers, so
PEV's own machinery and circuit breakers stay authoritative. Each is bounded by
a per-task attempt cap (``_MAX_NUDGES``); once capped it goes silent and defers
to PEV, so the worst case is a bounded number of extra turns, never a loop.

1. **Anti-clarification** (non-interactive runs only). Preventive: a short
   system-prompt note tells the model not to ask and to proceed on a stated
   assumption. Corrective: a prose turn that ends with ``?``, with no tool calls
   and no marker, is nudged to proceed. FALSE-POSITIVE RISK: a turn that
   legitimately ends with a question but wasn't a stall. Minimized by requiring
   ALL of {non-interactive, ends-with-``?`` after strip, no tool calls, no
   marker} — the "narrating ``X or Y? I'll use X.``" case ends with ``.`` and is
   excluded — and by the cap. Cost of a false positive: ONE extra "proceed"
   turn. Cost of the miss: a dead or long-spinning run. Rarer AND cheaper than
   the stall it prevents.

2. **Vacuous-completion rejection** (execute phase). ``EXECUTION COMPLETE`` with
   zero SUCCESSFUL file edits this task is not accepted; the model is nudged to
   either do the edits or, if the task genuinely needs none, re-affirm — after
   the cap the run defers to PEV (advance to verify, the honest read-only
   backstop). FALSE-POSITIVE RISK: a task that legitimately edits nothing
   (pure Q&A / inspection). Minimized because such tasks classify to ``fast``,
   not ``execute`` — to reach ``execute`` a task went through PLAN (classifier
   said complex, or the user forced the arc), where a 0-edit "done" is genuinely
   suspect — and because the nudge is confirm-or-act, not a hard block, and is
   capped. Cost of a false positive: one confirm turn. Cost of the miss: a
   silent "done" over unchanged files. Rarer AND cheaper.

3. **Marker tolerance** (bonus; plan/execute, on a retry only). A structurally
   COMPLETE response missing only the exact marker LINE (real numbered steps in
   plan; real edits in execute) is nudged to emit the precise marker, instead of
   spinning to the iteration cap. FALSE-POSITIVE RISK: nudging a response that
   only looks complete. Minimized by requiring ALL of {no marker, retry
   (``_pev_iteration >= 1`` — PEV already re-prompted once and it didn't help),
   structural completeness} and the cap. Cost of a false positive: one extra
   turn. Cost of the miss: spinning to the per-phase cap (5–15 turns) then
   failing. Rarer AND cheaper.

## Default-on, kill-switchable, zero-regression by construction

The factory attaches this middleware by default (the reviewer runs an unknown
model — robustness should not be opt-in) and omits it entirely when
``HCODE_PEV_ROBUSTNESS`` is ``0``/``false``/``off``. For a WELL-BEHAVED model —
real edits, clean markers, no questions — every ``after_model`` check falls
through to ``return None``, so PEV runs exactly as it does today: byte-identical
control flow (proven in ``tests/test_pev_robustness.py`` by driving the same
well-behaved script with and without the middleware and asserting identical
outcomes). HCode-side only; vendored deepagents untouched.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import HumanMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# PEV's completion/verdict markers (kept in sync with pev.py's _PHASE_MARKERS,
# read-only). Matched case-insensitively, exactly as PEV matches them, so a clean
# marker turn is recognised as "PEV will handle this" and deferred, never nudged.
_PHASE_MARKERS: tuple[str, ...] = ("PLAN COMPLETE", "EXECUTION COMPLETE", "VERIFIED OK", "ISSUES FOUND")

# Mutating file tools whose SUCCESSFUL results count as a real edit. Mirrors
# post_edit_lsp._MUTATING_FILE_TOOLS / SafetyGuard's file set: hcode's own
# edit/write/multi_edit plus the deepagents builtins (menu-excluded but still
# callable from memory, then re-homed by path containment — a successful one is
# still a real edit). Duplicated here (a tiny stable set) to avoid importing a
# private symbol across middleware modules.
_MUTATING_FILE_TOOLS: frozenset[str] = frozenset(
    {"write", "edit", "multi_edit", "write_file", "edit_file"}
)

_MAX_NUDGES: int = 2
"""Per-intervention, per-task attempt cap. After this many nudges of a given
kind the middleware goes silent for that kind and defers to PEV, so the worst
case is a bounded number of extra turns — never an unbounded loop. Two mirrors
the post-edit LSP gate's fix-attempt cap."""

# Numbered-step line (``1. ``, ``2) ``…) — the structural signal of a real plan.
_NUMBERED_STEP = re.compile(r"(?m)^\s*\d+[.)]\s+\S")

_ANTI_CLARIFY_PROMPT: str = (
    "## Non-interactive run\n\n"
    "This session is automated — there is no human available to answer "
    "questions. Do NOT ask the user for clarification or wait for confirmation. "
    "When something is ambiguous, choose the most reasonable interpretation, "
    "state the assumption you are making in one sentence, and proceed."
)

_CLARIFY_NUDGE: str = (
    "[automated run] No user is available to answer questions. Do not ask — "
    "choose the most reasonable interpretation, state your assumption in one "
    "sentence, and carry out the task now."
)

_VACUOUS_NUDGE: str = (
    "[automated check] You reported EXECUTION COMPLETE but no file has been "
    "edited yet in this task. If the task requires changes, make them now using "
    "the file tools. If it genuinely requires NO file changes, say so explicitly "
    "and then re-emit EXECUTION COMPLETE to confirm."
)


def _marker_nudge(marker: str) -> str:
    return (
        f"[automated check] You appear to have finished but did not emit the "
        f"required completion marker. If you are done, output exactly this line, "
        f"alone on its own line:\n{marker}"
    )


def _as_text(content: Any) -> str:
    """Normalise message content to text, matching PEV's own coercion."""
    return content if isinstance(content, str) else str(content)


def _last_ai_message(messages: list[Any]) -> Any | None:
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            return msg
    return None


def _successful_edit_count(messages: list[Any]) -> int:
    """Count SUCCESSFUL mutating-file-tool results in the message history.

    A tool call whose ToolMessage came back an error (status ``"error"`` or a
    body starting with ``Error``) does not count — "0 successful edits" is the
    signal, not "0 attempts". Correlates by ``tool_call_id`` so only results for
    the tracked mutating tools are considered.
    """
    tracked: dict[str, str] = {}
    for msg in messages:
        if getattr(msg, "type", None) != "ai":
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if name in _MUTATING_FILE_TOOLS and tc_id:
                tracked[tc_id] = name

    count = 0
    for msg in messages:
        if getattr(msg, "type", None) != "tool":
            continue
        if getattr(msg, "tool_call_id", None) not in tracked:
            continue
        if getattr(msg, "status", None) == "error":
            continue
        body = _as_text(getattr(msg, "content", ""))
        if body.lstrip().startswith("Error"):
            continue
        count += 1
    return count


def _looks_like_question(content: str) -> bool:
    """Conservative: True only when the turn ENDS with a question mark.

    Deliberately narrow — it excludes the "should I use X or Y? I'll use X."
    narration (ends with ``.``) the task called out as NOT a stall. Favouring
    false negatives is safe: a missed question just falls through to PEV's
    existing behaviour (status quo), never a regression.
    """
    stripped = content.strip()
    return bool(stripped) and stripped.endswith("?")


def _looks_structurally_complete(phase: str, content: str, messages: list[Any]) -> bool:
    """Strong, phase-specific signal that the model FINISHED but botched only the
    marker — the precondition for the marker-tolerance nudge.

    * execute: at least one SUCCESSFUL edit landed this task.
    * plan: at least two numbered steps (a real multi-step plan, not a fragment).
    """
    if phase == "execute":
        return _successful_edit_count(messages) > 0
    if phase == "plan":
        return len(_NUMBERED_STEP.findall(content)) >= 2
    return False


class PEVRobustnessMiddleware(AgentMiddleware):
    """Keep the PEV arc moving when the model asks a question, claims a vacuous
    completion, or finishes without the exact marker. Pre-empts PEV via
    ``jump_to`` (runs before PEV's ``after_model``); defers otherwise.

    ``non_interactive`` (from the factory's ``_is_interactive_stdin()``) gates
    the anti-clarification behaviour: in a real TTY (interactive CLI) asking the
    user is legitimate, so it is left untouched. Attached only when the factory
    decides to (default on, ``HCODE_PEV_ROBUSTNESS`` kill-switch); absent =
    byte-identical to today.
    """

    def __init__(self, non_interactive: bool = True) -> None:
        super().__init__()
        self.non_interactive = non_interactive
        # Per-task, per-kind nudge counters. Instance state is safe for the same
        # reason post_edit_lsp's is: one agent per thread, one task at a time
        # (daemon single-flight), reset each task in before_agent.
        self._nudges: dict[str, int] = {"clarify": 0, "vacuous": 0, "marker": 0}

    # ── Per-task reset ────────────────────────────────────────────────────────

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._nudges = {"clarify": 0, "vacuous": 0, "marker": 0}
        return None

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    # ── Preventive: anti-clarification system-prompt note (non-interactive) ────

    def _inject(self, request: ModelRequest) -> ModelRequest:
        if not self.non_interactive:
            return request
        from deepagents.middleware._utils import append_to_system_message
        return request.override(
            system_message=append_to_system_message(request.system_message, _ANTI_CLARIFY_PROMPT)
        )

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._inject(request))

    # ── Corrective: the three nudges (runs before PEV's after_model) ───────────

    def _nudge(self, kind: str, text: str, phase: str) -> dict[str, Any] | None:
        """Record + emit a nudge if under the per-kind cap, else defer (None)."""
        if self._nudges[kind] >= _MAX_NUDGES:
            logger.info("[pev-robustness] %s cap reached (phase=%s); deferring to PEV", kind, phase)
            return None
        self._nudges[kind] += 1
        # Logged without any task text or file content — just the kind, phase,
        # and attempt number, so an intervention is visible in a run's logs
        # without leaking what the user is working on.
        logger.info(
            "[pev-robustness] %s nudged (phase=%s, attempt=%d/%d)",
            kind, phase, self._nudges[kind], _MAX_NUDGES,
        )
        return {"jump_to": "model", "messages": [HumanMessage(content=text)]}

    def _intervene(self, state: dict) -> dict[str, Any] | None:
        phase = state.get("_pev_phase", "fast")
        last_ai = _last_ai_message(state.get("messages", []))
        if last_ai is None:
            return None

        content = _as_text(getattr(last_ai, "content", ""))
        upper = content.upper()
        has_tool_calls = bool(getattr(last_ai, "tool_calls", None))
        has_marker = any(m in upper for m in _PHASE_MARKERS)

        # Primary zero-regression guard: a turn doing real work (tool calls) is
        # never touched — defer straight to PEV.
        if has_tool_calls:
            return None

        # 1. Vacuous EXECUTION COMPLETE: marker present, zero successful edits.
        if phase == "execute" and "EXECUTION COMPLETE" in upper:
            if _successful_edit_count(state.get("messages", [])) == 0:
                return self._nudge("vacuous", _VACUOUS_NUDGE, phase)
            # Real edits → a legitimate completion → let PEV advance to verify.
            return None

        # 2. Any other CLEAN marker (PLAN COMPLETE / VERIFIED OK / ISSUES FOUND):
        #    PEV owns that transition — defer, unchanged behaviour.
        if has_marker:
            return None

        # 3. Clarifying question (non-interactive only): ends with '?', no marker.
        if self.non_interactive and _looks_like_question(content):
            return self._nudge("clarify", _CLARIFY_NUDGE, phase)

        # 4. Marker tolerance (bonus): structurally complete, markerless, on a
        #    retry (PEV already re-prompted once). Nudge for the exact marker.
        if phase in ("plan", "execute") and state.get("_pev_iteration", 0) >= 1:
            if _looks_structurally_complete(phase, content, state.get("messages", [])):
                marker = "PLAN COMPLETE" if phase == "plan" else "EXECUTION COMPLETE"
                return self._nudge("marker", _marker_nudge(marker), phase)

        return None  # everything else: defer to PEV (zero regression)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: dict, runtime: Runtime) -> dict[str, Any] | None:
        return self._intervene(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: dict, runtime: Runtime) -> dict[str, Any] | None:
        return self._intervene(state)


__all__ = ["PEVRobustnessMiddleware"]
