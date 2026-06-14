"""In-process unit tests for the daemon's per-session agent cache (Phase 2).

These do NOT spawn a subprocess: they instantiate JsonRpcDaemon directly so
``create_hcode_agent`` can be monkeypatched and call counts asserted. The
daemon's __init__ swaps ``sys.stdout`` for stderr, so a fixture restores it.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from unittest.mock import AsyncMock

from hcode_v2.daemon.server import JsonRpcDaemon


class _FakeAgent:
    """Stand-in agent whose astream_events yields no events."""

    async def astream_events(self, *args, **kwargs):  # noqa: D401
        if False:  # pragma: no cover - generator that never yields
            yield


@pytest.fixture
def restore_stdout():
    # JsonRpcDaemon.__init__ sets sys.stdout = sys.stderr; restore afterwards.
    orig = sys.stdout
    yield
    sys.stdout = orig


def test_run_task_builds_agent_once_per_thread(monkeypatch, restore_stdout):
    builder = AsyncMock(return_value=_FakeAgent())
    monkeypatch.setattr("hcode_v2.agent.factory.create_hcode_agent", builder)

    daemon = JsonRpcDaemon(mock=False)

    async def _drive() -> None:
        # Same thread_id twice -> agent built once, reused on the second call.
        await daemon._run_task(1, "task one", "thread-A")
        await daemon._run_task(2, "task two", "thread-A")
        assert builder.call_count == 1
        assert builder.call_args.kwargs["persist"] is True

        # A new thread_id -> a second build.
        await daemon._run_task(3, "task three", "thread-B")
        assert builder.call_count == 2

    asyncio.run(_drive())
