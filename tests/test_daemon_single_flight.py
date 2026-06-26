"""In-process test for the daemon's single-flight run_task guard (Phase 2).

A second run_task dispatched while one is still in flight must be rejected
with a JSON-RPC error rather than silently overwriting the in-flight task —
which would also open a same-session concurrent-write path. Driven in-process
so the in-flight task can be held open deterministically.
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


def test_second_concurrent_run_task_is_rejected(monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(mock=False)

    responses: list[tuple] = []
    monkeypatch.setattr(
        daemon,
        "send_response",
        lambda req_id, result=None, error=None: responses.append((req_id, result, error)),
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def _hang(req_id, task, thread_id, work_dir=None, active_skills=None):
        started.set()
        await release.wait()

    monkeypatch.setattr(daemon, "_run_task", _hang)

    async def _drive() -> None:
        await daemon._handle_run_task_dispatch(10, {"task": "first"})
        await started.wait()  # first task is genuinely in flight
        await daemon._handle_run_task_dispatch(11, {"task": "second"})
        # let the first task finish so the loop can shut down cleanly
        release.set()
        await daemon._current_task

    asyncio.run(_drive())

    busy = [r for r in responses if r[0] == 11]
    assert busy, "second run_task produced no response"
    _req_id, _result, error = busy[0]
    assert error is not None
    assert error["code"] == -32000
    assert "already running" in error["message"].lower()
