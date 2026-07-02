"""Tests for plan review (accept/reject) — middleware logic + daemon resume loop.

Middleware tests monkeypatch ``interrupt`` (it needs a live graph otherwise) to
check the decision→state-update mapping. Daemon tests drive a fake agent that
pauses with a plan-review interrupt, so the pause → emit → resume loop is covered
end-to-end without a model or a real graph.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from hcode_v2.agent import plan_review as pr
from hcode_v2.agent.plan_review import PlanReviewMiddleware, PLAN_REVIEW_INTERRUPT
from hcode_v2.daemon.server import JsonRpcDaemon


# ── Middleware: decision → state-update mapping ────────────────────────────────

def test_skips_when_not_execute_phase(monkeypatch):
    called = []
    monkeypatch.setattr(pr, "interrupt", lambda v: called.append(v) or {"accept": True})
    mw = PlanReviewMiddleware()
    # plan phase → no interrupt, no state change
    assert mw._maybe_review({"_pev_phase": "plan", "_pev_plan": "P"}) is None
    assert called == []   # interrupt NEVER fired outside the execute boundary


def test_skips_when_already_reviewed(monkeypatch):
    called = []
    monkeypatch.setattr(pr, "interrupt", lambda v: called.append(v) or {"accept": True})
    mw = PlanReviewMiddleware()
    # execute phase but already reviewed → no re-interrupt (one pause per plan)
    out = mw._maybe_review({"_pev_phase": "execute", "_plan_reviewed": True, "_pev_plan": "P"})
    assert out is None
    assert called == []


def test_accept_marks_reviewed_and_continues(monkeypatch):
    seen = {}
    monkeypatch.setattr(pr, "interrupt", lambda v: seen.update(v) or {"accept": True})
    mw = PlanReviewMiddleware()
    out = mw._maybe_review({"_pev_phase": "execute", "_pev_plan": "PLAN TEXT"})
    assert out == {"_plan_reviewed": True}          # continue into execute
    assert "jump_to" not in out                      # NOT stopped
    assert seen["type"] == PLAN_REVIEW_INTERRUPT     # the interrupt carried the plan
    assert seen["plan"] == "PLAN TEXT"


def test_reject_jumps_to_end(monkeypatch):
    monkeypatch.setattr(pr, "interrupt", lambda v: {"accept": False})
    mw = PlanReviewMiddleware()
    out = mw._maybe_review({"_pev_phase": "execute", "_pev_plan": "PLAN TEXT"})
    assert out["_plan_reviewed"] is True
    assert out["jump_to"] == "end"                   # clean stop before execute
    assert out["messages"] and "rejected" in out["messages"][0].content.lower()


def test_bare_bool_resume_value_is_accepted(monkeypatch):
    # Robustness: a resume value that is a plain truthy (not a dict) still accepts.
    monkeypatch.setattr(pr, "interrupt", lambda v: True)
    mw = PlanReviewMiddleware()
    out = mw._maybe_review({"_pev_phase": "execute", "_pev_plan": "P"})
    assert out == {"_plan_reviewed": True}


def test_falls_back_to_last_ai_message_when_no_pev_plan(monkeypatch):
    class _AI:
        type = "ai"
        content = "fallback plan body"
    seen = {}
    monkeypatch.setattr(pr, "interrupt", lambda v: seen.update(v) or {"accept": True})
    PlanReviewMiddleware()._maybe_review({"_pev_phase": "execute", "messages": [_AI()]})
    assert seen["plan"] == "fallback plan body"


# ── Daemon pause → emit → resume loop (fake agent) ─────────────────────────────

class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


class _FakeState:
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _AIMsg:
    def __init__(self, content):
        self.content = content


class FakePlanAgent:
    """Initial run pauses with a plan-review interrupt; the resume completes.

    astream_events(initial) → yields one innocuous event and marks the state
    paused. aget_state → reports the plan interrupt while paused. astream_events
    (Command resume) → records the resume value, clears the pause, completes.
    """

    def __init__(self, plan="PLAN FROM AGENT"):
        self.plan = plan
        self._paused = False
        self.resumed_with: list = []

    async def astream_events(self, input_, config=None, version=None):
        from langgraph.types import Command
        if isinstance(input_, Command):
            self.resumed_with.append(input_.resume)
            self._paused = False
            yield {"event": "on_chat_model_end", "data": {"output": _AIMsg("all done")}}
        else:
            self._paused = True
            yield {"event": "on_chain_start", "name": "agent", "data": {}}

    async def aget_state(self, config):
        if self._paused:
            return _FakeState([_FakeInterrupt({"type": PLAN_REVIEW_INTERRUPT, "plan": self.plan})])
        return _FakeState([])


@pytest.fixture
def restore_stdout():
    orig = sys.stdout
    yield
    sys.stdout = orig


def _wire_daemon(monkeypatch, fake_agent):
    daemon = JsonRpcDaemon(mock=False)
    events: list = []
    responses: list = []
    monkeypatch.setattr(daemon, "emit_event", lambda t, p=None: events.append((t, p)))
    monkeypatch.setattr(daemon, "send_response", lambda i, r=None, e=None: responses.append((i, r, e)))

    async def _fake_create(**kwargs):
        return fake_agent
    monkeypatch.setattr("hcode_v2.agent.factory.create_hcode_agent", _fake_create)
    # Keep fallback resolution inert regardless of the caller's environment.
    monkeypatch.delenv("HCODE_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("HCODE_MODEL_FALLBACK", raising=False)
    return daemon, events, responses


async def _wait_until_paused(daemon, tries=500):
    for _ in range(tries):
        await asyncio.sleep(0)
        if daemon._plan_decision_future is not None:
            return True
    return False


def test_daemon_plan_review_accept_resumes_execute(monkeypatch, restore_stdout):
    fake = FakePlanAgent()
    daemon, events, _ = _wire_daemon(monkeypatch, fake)

    async def _drive():
        await daemon._handle_run_task_dispatch(1, {"task": "build x", "thread_id": "t1", "plan_review": True})
        assert await _wait_until_paused(daemon), "run never paused for plan review"
        await daemon._handle_resume_plan(2, {"accept": True})
        await daemon._current_task

    asyncio.run(_drive())

    kinds = [t for t, _ in events]
    assert "plan_review" in kinds                      # the pause was surfaced
    plan_evt = next(p for t, p in events if t == "plan_review")
    assert plan_evt["plan"] == "PLAN FROM AGENT"        # with the plan text
    assert fake.resumed_with == [{"accept": True}]      # resumed to EXECUTE
    assert "plan_rejected" not in kinds
    assert daemon._plan_decision_future is None         # cleared after resume


def test_daemon_plan_review_reject_stops_cleanly(monkeypatch, restore_stdout):
    fake = FakePlanAgent()
    daemon, events, _ = _wire_daemon(monkeypatch, fake)

    async def _drive():
        await daemon._handle_run_task_dispatch(1, {"task": "build x", "thread_id": "t2", "plan_review": True})
        assert await _wait_until_paused(daemon)
        await daemon._handle_resume_plan(2, {"accept": False})
        await daemon._current_task

    asyncio.run(_drive())

    kinds = [t for t, _ in events]
    assert "plan_review" in kinds
    assert "plan_rejected" in kinds                     # reject surfaced
    assert fake.resumed_with == [{"accept": False}]     # graph resumed to jump→end


def test_daemon_plan_review_off_never_pauses(monkeypatch, restore_stdout):
    """plan_review omitted (default) → single stream, no interrupt check, no pause."""
    fake = FakePlanAgent()
    daemon, events, _ = _wire_daemon(monkeypatch, fake)

    async def _drive():
        await daemon._handle_run_task_dispatch(1, {"task": "build x", "thread_id": "t3"})  # no plan_review
        await daemon._current_task

    asyncio.run(_drive())

    kinds = [t for t, _ in events]
    assert "plan_review" not in kinds                   # never paused
    assert fake.resumed_with == []                      # never resumed
    # The fake would have "paused" internally, but the daemon never inspects
    # state when plan_review is off — proving zero added behaviour.


def test_resume_plan_with_no_pending_is_noop(monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(mock=False)
    responses: list = []
    monkeypatch.setattr(daemon, "send_response", lambda i, r=None, e=None: responses.append((i, r, e)))
    asyncio.run(daemon._handle_resume_plan(7, {"accept": True}))
    assert responses[0][1]["status"] == "no_pending_plan"


def test_abort_while_paused_for_review(monkeypatch, restore_stdout):
    """A run paused at plan review can still be ABORTED (coexistence)."""
    fake = FakePlanAgent()
    daemon, events, _ = _wire_daemon(monkeypatch, fake)

    async def _drive():
        await daemon._handle_run_task_dispatch(1, {"task": "build x", "thread_id": "t4", "plan_review": True})
        assert await _wait_until_paused(daemon)
        # Abort while paused — cancels the task that is awaiting the decision.
        await daemon._handle_abort(9)
        try:
            await daemon._current_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())

    kinds = [t for t, _ in events]
    assert "aborted" in kinds                           # abort won over the pause
    assert fake.resumed_with == []                      # never resumed to execute
    assert daemon._plan_decision_future is None         # future cleaned up
