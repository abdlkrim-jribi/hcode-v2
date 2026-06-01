"""Tests for the C2 streaming bridge.

Two layers:
  1. Unit tests  — feed raw LangGraph event dicts into StreamingBridge and
     assert the correct HcodeMessage events are emitted.  No model, no agent,
     no subprocess.
  2. Integration — launch the daemon in --mock mode as a subprocess and assert
     that run_task emits streaming_chunk + task_update events before done.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from hcode_v2.daemon.bridge import StreamingBridge


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_bridge(pev: bool = True) -> tuple[StreamingBridge, list[tuple[str, Any]]]:
    """Return a bridge wired to a list collector."""
    emitted: list[tuple[str, Any]] = []
    bridge = StreamingBridge(emit_fn=lambda t, p: emitted.append((t, p)), pev_mode=pev)
    return bridge, emitted


def _token_event(text: str, name: str = "ChatOpenAI") -> dict:
    """Build an on_chat_model_stream event dict."""
    chunk = MagicMock()
    chunk.content = text
    return {"event": "on_chat_model_stream", "name": name, "data": {"chunk": chunk}}


def _tool_start_event(tool_name: str, input_: dict) -> dict:
    return {"event": "on_tool_start", "name": tool_name, "data": {"input": input_}}


def _tool_end_event(tool_name: str, output: str = "ok") -> dict:
    return {"event": "on_tool_end", "name": tool_name, "data": {"output": output}}


# ── Unit: streaming_chunk ─────────────────────────────────────────────────────

def test_token_emits_streaming_chunk():
    bridge, emitted = _make_bridge()
    bridge.process_event(_token_event("Hello "))
    bridge.process_event(_token_event("world"))
    chunks = [p for t, p in emitted if t == "streaming_chunk"]
    assert len(chunks) == 2
    assert chunks[0]["content"] == "Hello "
    assert chunks[1]["content"] == "world"


def test_streaming_chunk_carries_phase_fast():
    bridge, emitted = _make_bridge(pev=False)
    bridge.process_event(_token_event("hi"))
    chunks = [p for t, p in emitted if t == "streaming_chunk"]
    assert chunks[0]["phase"] == "fast"


def test_streaming_chunk_carries_phase_plan_initially():
    bridge, emitted = _make_bridge(pev=True)
    bridge.process_event(_token_event("step one"))
    chunks = [p for t, p in emitted if t == "streaming_chunk"]
    assert chunks[0]["phase"] == "plan"


# ── Unit: tool events ─────────────────────────────────────────────────────────

def test_tool_start_emits_task_update():
    bridge, emitted = _make_bridge()
    bridge.process_event(_tool_start_event("write", {"path": "foo.py", "content": "x = 1"}))
    updates = [p for t, p in emitted if t == "task_update"]
    assert len(updates) == 1
    assert updates[0]["step"] == "tool:write"
    assert "write" in updates[0]["markdown"]
    assert "foo.py" in updates[0]["markdown"]


def test_tool_end_emits_task_update():
    bridge, emitted = _make_bridge()
    bridge.process_event(_tool_start_event("bash", {"command": "ls"}))
    bridge.process_event(_tool_end_event("bash", "file.py"))
    steps = [p["step"] for t, p in emitted if t == "task_update"]
    assert "tool:bash" in steps
    assert "tool_result:bash" in steps


def test_tool_without_path():
    bridge, emitted = _make_bridge()
    bridge.process_event(_tool_start_event("read", {"path": "README.md"}))
    update = next(p for t, p in emitted if t == "task_update")
    assert "read" in update["markdown"]


# ── Unit: PEV phase transitions ───────────────────────────────────────────────

def test_planning_started_on_task_start():
    bridge, emitted = _make_bridge(pev=True)
    bridge.on_task_start()
    types = [t for t, _ in emitted]
    assert "planning_started" in types


def test_no_planning_started_in_fast_mode():
    bridge, emitted = _make_bridge(pev=False)
    bridge.on_task_start()
    types = [t for t, _ in emitted]
    assert "planning_started" not in types


def test_plan_complete_marker_triggers_plan_created_and_execution_started():
    bridge, emitted = _make_bridge(pev=True)
    # Stream tokens including the PEV marker
    for token in ["Step 1: analyse.", " ", "PLAN COMPLETE"]:
        bridge.process_event(_token_event(token))

    types = [t for t, _ in emitted]
    assert "plan_created" in types
    assert "execution_started" in types


def test_plan_created_payload_contains_plan_text():
    bridge, emitted = _make_bridge(pev=True)
    bridge.process_event(_token_event("My plan. PLAN COMPLETE"))
    plan_evts = [p for t, p in emitted if t == "plan_created"]
    assert plan_evts, "plan_created not emitted"
    assert "My plan" in plan_evts[0]["markdown"]


def test_execution_started_phase_updates_subsequent_chunks():
    bridge, emitted = _make_bridge(pev=True)
    bridge.process_event(_token_event("PLAN COMPLETE"))   # triggers phase flip
    bridge.process_event(_token_event("executing now"))
    exec_chunks = [p for t, p in emitted if t == "streaming_chunk" and p["phase"] == "execute"]
    assert exec_chunks, "No streaming_chunk with phase=execute after PLAN COMPLETE"


def test_execution_complete_marker_triggers_verification_started():
    bridge, emitted = _make_bridge(pev=True)
    bridge.process_event(_token_event("PLAN COMPLETE"))
    bridge.process_event(_token_event("did work. EXECUTION COMPLETE"))

    types = [t for t, _ in emitted]
    assert "verification_started" in types


def test_phase_transitions_not_triggered_in_fast_mode():
    bridge, emitted = _make_bridge(pev=False)
    bridge.process_event(_token_event("PLAN COMPLETE"))
    bridge.process_event(_token_event("EXECUTION COMPLETE"))
    types = [t for t, _ in emitted]
    assert "plan_created" not in types
    assert "execution_started" not in types
    assert "verification_started" not in types


def test_plan_complete_fires_only_once():
    bridge, emitted = _make_bridge(pev=True)
    bridge.process_event(_token_event("PLAN COMPLETE"))
    bridge.process_event(_token_event("PLAN COMPLETE again"))
    assert [t for t, _ in emitted if t == "plan_created"].count("plan_created") <= 1  # type: ignore[operator]
    plan_created_count = sum(1 for t, _ in emitted if t == "plan_created")
    assert plan_created_count == 1


# ── Unit: finalize ────────────────────────────────────────────────────────────

def test_finalize_emits_done():
    bridge, emitted = _make_bridge()
    bridge.finalize("all done")
    done_evts = [p for t, p in emitted if t == "done"]
    assert done_evts
    assert done_evts[0]["summary"] == "all done"


def test_finalize_done_has_timestamp():
    bridge, emitted = _make_bridge()
    bridge.finalize()
    done_evts = [p for t, p in emitted if t == "done"]
    assert "timestamp" in done_evts[0]


# ── Unit: unknown events silently ignored ─────────────────────────────────────

def test_unknown_event_kind_ignored():
    bridge, emitted = _make_bridge()
    bridge.process_event({"event": "on_retriever_start", "name": "x", "data": {}})
    assert emitted == []


# ── Integration: daemon mock streaming ───────────────────────────────────────

def _start_daemon(tmp_path: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "hcode_v2.daemon", "--mock", "--work-dir", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(tmp_path),
    )
    ready = json.loads(proc.stdout.readline().decode())
    assert ready == {"type": "ready"}
    return proc


def _send(proc: subprocess.Popen, method: str, params: dict | None = None, req_id: int = 1) -> None:
    req: dict = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params:
        req["params"] = params
    proc.stdin.write((json.dumps(req) + "\n").encode())
    proc.stdin.flush()


def _read(proc: subprocess.Popen) -> dict:
    return json.loads(proc.stdout.readline().decode())


def _drain_until_done(proc: subprocess.Popen, max_lines: int = 50) -> list[dict]:
    """Read lines from daemon stdout until a 'done' event or max_lines reached."""
    collected = []
    for _ in range(max_lines):
        msg = _read(proc)
        collected.append(msg)
        if msg.get("type") == "done":
            break
    return collected


def test_daemon_run_task_emits_streaming_chunks(tmp_path: Path):
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "run_task", {"task": "write hello.py"}, req_id=1)

        # First line is the JSON-RPC "started" response
        started = _read(proc)
        assert started["result"]["status"] == "started"

        # Collect all subsequent events until done
        events = _drain_until_done(proc)
        types = [e.get("type") for e in events]

        assert "streaming_chunk" in types, f"No streaming_chunk in: {types}"
        assert "done" in types, f"No done event in: {types}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_daemon_run_task_emits_tool_update(tmp_path: Path):
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "run_task", {"task": "do something"}, req_id=2)
        _read(proc)  # started response
        events = _drain_until_done(proc)
        task_updates = [e for e in events if e.get("type") == "task_update"]
        assert len(task_updates) >= 2, f"Expected ≥2 task_update events, got: {task_updates}"
        steps = [e["payload"]["step"] for e in task_updates]
        assert any(s.startswith("tool:") for s in steps), f"No tool-start step in: {steps}"
        assert any(s.startswith("tool_result:") for s in steps), f"No tool-end step in: {steps}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_daemon_run_task_emits_phase_events(tmp_path: Path):
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "run_task", {"task": "build something"}, req_id=3)
        _read(proc)  # started
        events = _drain_until_done(proc)
        types = [e.get("type") for e in events]
        assert "planning_started" in types
        assert "execution_started" in types
        assert "verification_started" in types
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_daemon_run_task_streaming_chunk_has_phase(tmp_path: Path):
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "run_task", {"task": "x"}, req_id=4)
        _read(proc)  # started
        events = _drain_until_done(proc)
        chunks = [e for e in events if e.get("type") == "streaming_chunk"]
        assert chunks, "No streaming_chunk events"
        for c in chunks:
            assert "phase" in c["payload"], f"streaming_chunk missing phase: {c}"
            assert c["payload"]["phase"] in {"plan", "execute", "verify", "fast"}
    finally:
        proc.terminate()
        proc.wait(timeout=5)
