"""Subprocess tests for the JSON-RPC daemon (mock mode — no LLM calls required)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ── Subprocess helpers ────────────────────────────────────────────────────────

def _start_daemon(tmp_path: Path) -> subprocess.Popen:
    """Launch daemon in --mock mode in tmp_path, consume the initial 'ready' event."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "hcode_v2.daemon", "--mock", "--work-dir", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(tmp_path),
    )
    ready = _read(proc)
    assert ready == {"type": "ready"}, f"Unexpected first line: {ready}"
    return proc


def _send(proc: subprocess.Popen, method: str, params: dict | None = None, req_id: int = 1) -> None:
    req: dict = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params:
        req["params"] = params
    line = (json.dumps(req) + "\n").encode()
    proc.stdin.write(line)
    proc.stdin.flush()


def _read(proc: subprocess.Popen) -> dict:
    raw = proc.stdout.readline()
    return json.loads(raw.decode())


# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def daemon(tmp_path: Path):
    proc = _start_daemon(tmp_path)
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


# ── health ────────────────────────────────────────────────────────────────────

def test_health(daemon):
    _send(daemon, "health", req_id=1)
    resp = _read(daemon)
    assert resp["id"] == 1
    assert resp["result"]["status"] == "running"
    assert resp["result"]["mock"] is True


# ── list_skills ───────────────────────────────────────────────────────────────

# The built-in skills are install-relative, so they appear from ANY work_dir
# (incl. the daemon's chdir into the user's folder). We assert their presence via
# a known member rather than the full set, so adding a built-in won't break these.
_BUILTIN_SKILL = "clean-code"


def test_list_skills_includes_builtins_from_any_workdir(daemon):
    # daemon runs with --work-dir = an empty tmp_path; built-ins must still appear.
    _send(daemon, "list_skills", req_id=2)
    resp = _read(daemon)
    assert resp["id"] == 2
    assert _BUILTIN_SKILL in resp["result"]["skills"]


def test_list_skills_merges_project_local_with_builtins(tmp_path: Path):
    skill = tmp_path / ".hcode" / "skills" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# My Skill")
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "list_skills", req_id=20)
        resp = _read(proc)
        skills = resp["result"]["skills"]
        # Project-local skill appears ALONGSIDE the built-ins (union).
        assert "my-skill" in skills
        assert _BUILTIN_SKILL in skills
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_list_skills_ignores_non_skill_dirs(tmp_path: Path):
    # A project-local directory without SKILL.md is excluded; built-ins remain.
    non_skill = tmp_path / ".hcode" / "skills" / "not-a-skill"
    non_skill.mkdir(parents=True)
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "list_skills", req_id=21)
        resp = _read(proc)
        skills = resp["result"]["skills"]
        assert "not-a-skill" not in skills
        assert _BUILTIN_SKILL in skills
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ── list_workflows ────────────────────────────────────────────────────────────

def test_list_workflows_empty(daemon):
    _send(daemon, "list_workflows", req_id=3)
    resp = _read(daemon)
    assert resp["id"] == 3
    assert resp["result"]["workflows"] == []


def test_list_workflows_finds_md_files(tmp_path: Path):
    wf = tmp_path / ".hcode" / "workflows"
    wf.mkdir(parents=True)
    (wf / "deploy.md").write_text("# Deploy")
    (wf / "test.md").write_text("# Test")
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "list_workflows", req_id=30)
        resp = _read(proc)
        assert resp["result"]["workflows"] == ["deploy", "test"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ── list_sessions ─────────────────────────────────────────────────────────────

def test_list_sessions_empty(daemon):
    _send(daemon, "list_sessions", req_id=8)
    resp = _read(daemon)
    assert resp["id"] == 8
    assert resp["result"]["sessions"] == []


def test_list_sessions_finds_dbs(tmp_path: Path):
    sessions = tmp_path / ".hcode" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "a.db").write_text("")
    (sessions / "b.db").write_text("")
    proc = _start_daemon(tmp_path)
    try:
        _send(proc, "list_sessions", req_id=80)
        resp = _read(proc)
        assert resp["result"]["sessions"] == ["a", "b"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ── list_mcp_servers ──────────────────────────────────────────────────────────

def test_list_mcp_servers(daemon):
    _send(daemon, "list_mcp_servers", req_id=4)
    resp = _read(daemon)
    assert resp["id"] == 4
    assert isinstance(resp["result"]["servers"], list)
    # KNOWN_SERVERS catalog is non-empty
    assert len(resp["result"]["servers"]) > 0
    # Each entry has at minimum a "name" key
    for s in resp["result"]["servers"]:
        assert "name" in s


# ── connect_mcp_server ────────────────────────────────────────────────────────

def test_connect_mcp_server_unknown_returns_error(daemon):
    _send(daemon, "connect_mcp_server", {"server": "definitely-not-real"}, req_id=5)
    resp = _read(daemon)
    assert resp["id"] == 5
    assert "error" in resp
    assert resp["error"]["code"] == -32602


def test_connect_mcp_server_known(tmp_path: Path):
    proc = _start_daemon(tmp_path)
    try:
        # Get a real server name from the catalog
        _send(proc, "list_mcp_servers", req_id=50)
        servers_resp = _read(proc)
        first_server = servers_resp["result"]["servers"][0]["name"]

        _send(proc, "connect_mcp_server", {"server": first_server}, req_id=51)
        resp = _read(proc)
        assert resp["id"] == 51
        assert resp["result"]["status"] == "connected"
        assert resp["result"]["server"] == first_server
        # Config file should be created
        assert (tmp_path / ".hcode" / "mcp_config.json").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ── run_task ──────────────────────────────────────────────────────────────────

def _drain_until_done(proc: subprocess.Popen, max_lines: int = 30) -> list[dict]:
    """Read event lines until a 'done' notification or max_lines reached."""
    events = []
    for _ in range(max_lines):
        msg = _read(proc)
        events.append(msg)
        if msg.get("type") == "done":
            break
    return events


def test_run_task_returns_started_then_done(daemon):
    # C2 mock emits streaming events before done; drain until we see it
    _send(daemon, "run_task", {"task": "say hello"}, req_id=6)
    started = _read(daemon)
    assert started["id"] == 6
    assert started["result"]["status"] == "started"
    assert "thread_id" in started["result"]

    events = _drain_until_done(daemon)
    done_evts = [e for e in events if e.get("type") == "done"]
    assert done_evts, f"No done event in stream: {[e.get('type') for e in events]}"
    done_evt = done_evts[0]
    assert done_evt["payload"]["summary"] or done_evt.get("payload") is not None


def test_run_task_custom_thread_id(daemon):
    # A client-supplied thread_id passes through verbatim (no gui_ prefix added),
    # so the GUI can resume a named or CLI-created session.
    _send(daemon, "run_task", {"task": "ping", "thread_id": "my-thread"}, req_id=60)
    started = _read(daemon)
    assert started["result"]["thread_id"] == "my-thread"
    _drain_until_done(daemon)  # consume all events including done


def test_run_task_auto_thread_id_has_gui_prefix(daemon):
    # No client thread_id -> daemon auto-generates one namespaced under gui_.
    _send(daemon, "run_task", {"task": "say hello"}, req_id=61)
    started = _read(daemon)
    assert started["result"]["thread_id"].startswith("gui_")
    _drain_until_done(daemon)


# ── run_workflow ──────────────────────────────────────────────────────────────

def test_run_workflow_returns_started_then_done(daemon):
    # C2 mock emits streaming events before done; drain until we see it
    _send(daemon, "run_workflow", {"workflow": "build"}, req_id=7)
    started = _read(daemon)
    assert started["id"] == 7
    assert started["result"]["status"] == "started"

    events = _drain_until_done(daemon)
    done_evts = [e for e in events if e.get("type") == "done"]
    assert done_evts, f"No done event: {[e.get('type') for e in events]}"


def test_run_workflow_auto_thread_id_has_gui_wf_prefix(daemon):
    _send(daemon, "run_workflow", {"workflow": "build"}, req_id=71)
    started = _read(daemon)
    assert started["result"]["thread_id"].startswith("gui_wf_")
    _drain_until_done(daemon)


# ── unknown method ────────────────────────────────────────────────────────────

def test_unknown_method_returns_32601(daemon):
    _send(daemon, "does_not_exist", req_id=10)
    resp = _read(daemon)
    assert resp["id"] == 10
    assert "error" in resp
    assert resp["error"]["code"] == -32601


# ── shutdown ──────────────────────────────────────────────────────────────────

def test_shutdown(daemon):
    _send(daemon, "shutdown", req_id=99)
    resp = _read(daemon)
    assert resp["id"] == 99
    assert resp["result"]["status"] == "shutting_down"
