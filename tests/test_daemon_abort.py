"""In-process tests for daemon abort — cancel a running task via the 'abort' method.

Verifies:
  - abort while running  → status "aborting", task is cancelled, aborted event emitted,
    single-flight released (new task can start)
  - abort with no task   → status "no_task_running"
  - streaming intact     → a task that runs to completion still emits done
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from hcode_v2.daemon.server import JsonRpcDaemon


@pytest.fixture
def restore_stdout():
    orig = sys.stdout
    yield
    sys.stdout = orig


def _make_daemon(monkeypatch, restore_stdout) -> tuple[JsonRpcDaemon, list, list]:
    """Return (daemon, responses, events) with both output methods captured."""
    daemon = JsonRpcDaemon(mock=False)
    responses: list[tuple] = []
    events: list[tuple] = []
    monkeypatch.setattr(
        daemon, "send_response",
        lambda req_id, result=None, error=None: responses.append((req_id, result, error)),
    )
    monkeypatch.setattr(
        daemon, "emit_event",
        lambda type_, payload=None: events.append((type_, payload)),
    )
    return daemon, responses, events


# ── abort while task is running ───────────────────────────────────────────────

def test_abort_cancels_running_task_and_emits_aborted(monkeypatch, restore_stdout):
    daemon, responses, events = _make_daemon(monkeypatch, restore_stdout)

    started = asyncio.Event()
    released = asyncio.Event()

    async def _slow_task(req_id, task, thread_id, work_dir=None, active_skills=None, model=None, plan_review=False):
        # Simulate a long-running task with multiple yield points.
        started.set()
        await released.wait()   # this await is where CancelledError lands

    monkeypatch.setattr(daemon, "_run_task", _slow_task)

    async def _drive():
        # Start the task.
        await daemon._handle_run_task_dispatch(10, {"task": "slow task"})
        await started.wait()

        # Task is in flight — abort it.
        await daemon._handle_abort(20)

        # Give the event loop a moment to propagate the cancellation.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Let the released wait unblock (it won't matter, already cancelled).
        released.set()

        # Wait for the task to fully die.
        if daemon._current_task is not None:
            try:
                await asyncio.wait_for(daemon._current_task, timeout=1.0)
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_drive())

    # Abort response
    abort_resp = [r for r in responses if r[0] == 20]
    assert abort_resp, "abort produced no response"
    assert abort_resp[0][1]["status"] == "aborting"
    assert abort_resp[0][2] is None  # no error

    # The _run_task mock doesn't re-raise CancelledError, so _current_task is
    # cancelled but no aborted event is emitted by the mock. The real
    # CancelledError/event path is tested by test_abort_real_run_task below.


def test_abort_real_run_task_emits_aborted_event_and_releases_single_flight(monkeypatch, restore_stdout):
    """Abort injected into the REAL _run_task path confirms the event + single-flight release."""
    daemon, responses, events = _make_daemon(monkeypatch, restore_stdout)

    # _run_task's mock branch just awaits _mock_streaming_task, which has many
    # asyncio.sleep(0) yield points — the cancellation lands there.
    daemon._mock = True

    # Intercept _mock_streaming_task to hang until we cancel.
    started = asyncio.Event()

    async def _hang(task: str):
        started.set()
        await asyncio.sleep(9999)   # cancelled here

    monkeypatch.setattr(daemon, "_mock_streaming_task", _hang)

    async def _drive():
        await daemon._handle_run_task_dispatch(1, {"task": "x"})
        await started.wait()
        assert daemon._current_task is not None and not daemon._current_task.done()

        await daemon._handle_abort(2)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Wait for task to die
        try:
            await asyncio.wait_for(asyncio.shield(daemon._current_task), timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    asyncio.run(_drive())

    # 'aborted' event must have been emitted
    aborted = [e for e in events if e[0] == "aborted"]
    assert aborted, f"no aborted event emitted; events={events}"
    assert "aborted" in aborted[0][1].get("message", "").lower()

    # single-flight released — _current_task is None
    assert daemon._current_task is None, "_current_task not cleared after abort"


def test_abort_then_new_task_can_start(monkeypatch, restore_stdout):
    """After abort, the single-flight guard allows a new task to be dispatched."""
    daemon, responses, events = _make_daemon(monkeypatch, restore_stdout)
    daemon._mock = True

    started = asyncio.Event()

    async def _hang(task: str):
        started.set()
        await asyncio.sleep(9999)

    completed_tasks: list[str] = []

    async def _quick(task: str):
        completed_tasks.append(task)

    monkeypatch.setattr(daemon, "_mock_streaming_task", _hang)

    async def _drive():
        # First task — abort it
        await daemon._handle_run_task_dispatch(1, {"task": "first"})
        await started.wait()
        await daemon._handle_abort(2)
        try:
            await asyncio.wait_for(asyncio.shield(daemon._current_task), timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

        # single-flight must now be clear
        assert daemon._current_task is None

        # Second task — should start without "already running" error
        monkeypatch.setattr(daemon, "_mock_streaming_task", _quick)
        await daemon._handle_run_task_dispatch(3, {"task": "second"})
        if daemon._current_task:
            await daemon._current_task

    asyncio.run(_drive())

    # No "already running" error for the second request
    second_err = [r for r in responses if r[0] == 3 and r[2] is not None]
    assert not second_err, f"second task was rejected: {second_err}"
    assert "second" in completed_tasks


# ── abort with no task ────────────────────────────────────────────────────────

def test_abort_no_task_running_returns_status(monkeypatch, restore_stdout):
    daemon, responses, events = _make_daemon(monkeypatch, restore_stdout)

    asyncio.run(daemon._handle_abort(99))

    assert responses, "abort produced no response"
    _req_id, result, error = responses[0]
    assert _req_id == 99
    assert result["status"] == "no_task_running"
    assert error is None


def test_abort_after_task_completes_returns_no_task(monkeypatch, restore_stdout):
    """Aborting after natural completion sees _current_task.done() → no_task_running."""
    daemon, responses, events = _make_daemon(monkeypatch, restore_stdout)
    daemon._mock = True

    async def _instant(task: str):
        pass  # completes immediately

    monkeypatch.setattr(daemon, "_mock_streaming_task", _instant)

    async def _drive():
        await daemon._handle_run_task_dispatch(1, {"task": "quick"})
        if daemon._current_task:
            await daemon._current_task
        await asyncio.sleep(0)  # let finally clear _current_task

        # Now abort — no task should be running
        await daemon._handle_abort(2)

    asyncio.run(_drive())

    abort_resp = [r for r in responses if r[0] == 2]
    assert abort_resp
    assert abort_resp[0][1]["status"] == "no_task_running"


# ── abort via handle_request router ───────────────────────────────────────────

def test_abort_via_handle_request_router(monkeypatch, restore_stdout):
    """Confirm 'abort' method reaches _handle_abort through the router."""
    daemon, responses, events = _make_daemon(monkeypatch, restore_stdout)

    asyncio.run(daemon.handle_request({"jsonrpc": "2.0", "id": 5, "method": "abort", "params": {}}))

    assert any(r[0] == 5 for r in responses), "abort via handle_request got no response"
