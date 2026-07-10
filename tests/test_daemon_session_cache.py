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


def test_run_task_evicts_agent_when_active_skills_change(monkeypatch, restore_stdout):
    """Changing active_skills mid-session forces a rebuild (baked in at build time)."""
    builder = AsyncMock(return_value=_FakeAgent())
    monkeypatch.setattr("hcode_v2.agent.factory.create_hcode_agent", builder)

    daemon = JsonRpcDaemon(mock=False)

    async def _drive() -> None:
        # First call: no skills filter (None = all).
        await daemon._run_task(1, "task", "tid", active_skills=None)
        assert builder.call_count == 1
        assert builder.call_args.kwargs["active_skills"] is None

        # Same skills → reuse the cached agent.
        await daemon._run_task(2, "task", "tid", active_skills=None)
        assert builder.call_count == 1

        # Different skills → evict + rebuild.
        await daemon._run_task(3, "task", "tid", active_skills=["clean-code"])
        assert builder.call_count == 2
        assert builder.call_args.kwargs["active_skills"] == ["clean-code"]

        # Same selection again → reuse.
        await daemon._run_task(4, "task", "tid", active_skills=["clean-code"])
        assert builder.call_count == 2

        # Reset to all (None) → evict + rebuild.
        await daemon._run_task(5, "task", "tid", active_skills=None)
        assert builder.call_count == 3
        assert builder.call_args.kwargs["active_skills"] is None

    asyncio.run(_drive())


def test_run_task_evicts_agent_when_model_changes(monkeypatch, restore_stdout):
    """Switching the selected model mid-session forces a rebuild on the next task."""
    builder = AsyncMock(return_value=_FakeAgent())
    monkeypatch.setattr("hcode_v2.agent.factory.create_hcode_agent", builder)

    daemon = JsonRpcDaemon(mock=False)

    async def _drive() -> None:
        # First call: no model override (None = .env default).
        await daemon._run_task(1, "task", "tid", model=None)
        assert builder.call_count == 1
        assert builder.call_args.kwargs["model"] is None

        # Same (default) model → reuse the cached agent.
        await daemon._run_task(2, "task", "tid", model=None)
        assert builder.call_count == 1

        # Different model → evict + rebuild, and the new id reaches the factory.
        await daemon._run_task(3, "task", "tid", model="qwen/qwen-2.5-coder")
        assert builder.call_count == 2
        assert builder.call_args.kwargs["model"] == "qwen/qwen-2.5-coder"

        # Same model again → reuse.
        await daemon._run_task(4, "task", "tid", model="qwen/qwen-2.5-coder")
        assert builder.call_count == 2

        # Back to default (None) → evict + rebuild.
        await daemon._run_task(5, "task", "tid", model=None)
        assert builder.call_count == 3

    asyncio.run(_drive())


def test_run_task_dispatch_validates_model_param(monkeypatch, restore_stdout):
    """Non-string / blank model params degrade to None (the default) — no crash."""
    captured: list = []

    async def _capture(req_id, task, thread_id, work_dir=None, active_skills=None, model=None, plan_review=False, force_plan=False):
        captured.append(model)

    daemon = JsonRpcDaemon(mock=False)
    monkeypatch.setattr(daemon, "_run_task", _capture)

    async def _drive() -> None:
        for params, expected in [
            ({"task": "t"}, None),                       # absent → None
            ({"task": "t", "model": ""}, None),          # blank → None
            ({"task": "t", "model": "  "}, None),        # whitespace → None
            ({"task": "t", "model": 123}, None),         # wrong type → None
            ({"task": "t", "model": "free/m"}, "free/m"),# valid → passed through
        ]:
            await daemon._handle_run_task_dispatch(1, params)
            if daemon._current_task:
                await daemon._current_task
        assert captured == [None, None, None, None, "free/m"]

    asyncio.run(_drive())


def test_run_task_evicts_agent_when_mcp_config_changes(tmp_path, monkeypatch, restore_stdout):
    """Connecting/disconnecting an MCP server changes the config signature →
    the cached agent evicts and rebuilds so the new tools actually reach it."""
    import json

    builder = AsyncMock(return_value=_FakeAgent())
    monkeypatch.setattr("hcode_v2.agent.factory.create_hcode_agent", builder)

    cfg = tmp_path / "mcp.json"
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(cfg))

    async def _drive() -> None:
        # First task: no MCP servers configured.
        await daemon._run_task(1, "task", "tid")
        assert builder.call_count == 1

        # Same (empty) MCP config → reuse the cached agent.
        await daemon._run_task(2, "task", "tid")
        assert builder.call_count == 1

        # A server gets connected → config signature changes → evict + rebuild.
        cfg.write_text(json.dumps({"servers": {"web-fetch": {"command": "npx", "args": []}}}))
        await daemon._run_task(3, "task", "tid")
        assert builder.call_count == 2

        # Same config again → reuse.
        await daemon._run_task(4, "task", "tid")
        assert builder.call_count == 2

        # Disconnect (server removed) → signature changes → evict + rebuild.
        cfg.write_text(json.dumps({"servers": {}}))
        await daemon._run_task(5, "task", "tid")
        assert builder.call_count == 3

    asyncio.run(_drive())
