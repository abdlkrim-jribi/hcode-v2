"""W3.3 — LSP diagnostics wired into PEV Verify.

Two layers:

  1. PEV side — drive ``PEVMiddleware.awrap_model_call`` with a *mock* provider
     and assert: errors are injected into the verify prompt, clean/no-provider
     leave it identical (optional degradation), the provider only runs in Verify,
     it receives the edited-file list, exceptions degrade gracefully, and the
     markers/circuit-breaker logic is unchanged.
  2. Provider side — drive ``lsp_tools.verify_diagnostics_addendum`` with a
     *mock* LSP client and assert the ERRORS-ONLY rule (warnings never trigger
     feedback), clean/no-server/unsupported all return ``None``.

Everything is mocked — no real pyright, no subprocess — so this is CI-safe.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

from deepagents.middleware.pev import PEVMiddleware, _collect_modified_files
from hcode_v2.tools import lsp_tools
from hcode_v2.lsp.protocol import Diagnostic, DiagnosticSeverity, Position, Range


# ── PEV-side helpers ──────────────────────────────────────────────────────────

def _runtime() -> MagicMock:
    rt = MagicMock()
    rt.context = {}
    rt.store = None
    del rt.config
    return rt


def _model() -> MagicMock:
    m = MagicMock()
    m._llm_type = "test-model"
    m.profile = {"max_input_tokens": 100000}
    m._get_ls_params.return_value = {"ls_provider": "test"}
    return m


def _state(messages, *, phase="verify"):
    return {
        "messages": messages,
        "_pev_phase": phase,
        "_pev_iteration": 0,
        "_pev_error_count": 0,
        "_pev_plan": None,
        "_pev_recent_hashes": [],
        "_pev_task": "fix the bug",
    }


def _request(state) -> ModelRequest:
    return ModelRequest(
        model=_model(),
        messages=state.get("messages", []),
        system_message=None,
        runtime=_runtime(),
        state=state,
    )


def _sys_text(request: ModelRequest) -> str:
    sm = request.system_message
    if sm is None:
        return ""
    return " ".join(b.get("text", "") for b in sm.content_blocks if b.get("type") == "text")


async def _run_awrap(mw: PEVMiddleware, request: ModelRequest) -> ModelRequest:
    captured: list[ModelRequest] = []

    async def handler(req: ModelRequest) -> AIMessage:
        captured.append(req)
        return AIMessage(content="ok")

    await mw.awrap_model_call(request, handler)
    return captured[0]


def _edited(*paths: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "edit", "args": {"path": p}, "id": f"tc{i}"} for i, p in enumerate(paths)],
    )


# ── _collect_modified_files ───────────────────────────────────────────────────

def test_collect_modified_files_from_tool_calls():
    state = _state([
        HumanMessage(content="fix"),
        AIMessage(content="", tool_calls=[
            {"name": "write", "args": {"path": "a.py"}, "id": "1"},
            {"name": "edit", "args": {"path": "b.py"}, "id": "2"},
            {"name": "multi_edit", "args": {"path": "c.py"}, "id": "3"},
        ]),
    ])
    assert _collect_modified_files(state) == ["a.py", "b.py", "c.py"]


def test_collect_modified_files_ignores_non_file_tools():
    state = _state([
        AIMessage(content="", tool_calls=[
            {"name": "read", "args": {"path": "x.py"}, "id": "1"},
            {"name": "bash", "args": {"command": "ls"}, "id": "2"},
            {"name": "grep", "args": {"pattern": "x"}, "id": "3"},
        ]),
    ])
    assert _collect_modified_files(state) == []


def test_collect_modified_files_dedups_preserving_order():
    state = _state([
        AIMessage(content="", tool_calls=[{"name": "edit", "args": {"path": "a.py"}, "id": "1"}]),
        AIMessage(content="", tool_calls=[{"name": "write", "args": {"path": "a.py"}, "id": "2"}]),
        AIMessage(content="", tool_calls=[{"name": "edit", "args": {"path": "z.py"}, "id": "3"}]),
    ])
    assert _collect_modified_files(state) == ["a.py", "z.py"]


# ── PEV injection behaviour ───────────────────────────────────────────────────

async def test_errors_are_injected_into_verify_prompt():
    async def provider(files):
        return "LSP_ERRORS_BLOCK: type error at line 3"

    mw = PEVMiddleware(diagnostics_provider=provider)
    captured = await _run_awrap(mw, _request(_state([HumanMessage("fix"), _edited("buggy.py")])))
    text = _sys_text(captured)
    assert "LSP_ERRORS_BLOCK" in text          # diagnostics surfaced
    assert "VERIFIED OK" in text and "ISSUES FOUND" in text  # markers preserved


async def test_clean_provider_leaves_verify_prompt_unchanged():
    async def provider(files):
        return None  # no errors

    mw = PEVMiddleware(diagnostics_provider=provider)
    captured = await _run_awrap(mw, _request(_state([HumanMessage("fix"), _edited("ok.py")])))
    text = _sys_text(captured)
    assert "LSP_ERRORS_BLOCK" not in text
    assert "VERIFIED OK" in text  # standard verify prompt intact


async def test_no_provider_behaves_exactly_as_before():
    """Optional-degradation: default PEVMiddleware() never touches the request."""
    mw = PEVMiddleware()  # no provider
    captured = await _run_awrap(mw, _request(_state([HumanMessage("fix"), _edited("x.py")])))
    text = _sys_text(captured)
    assert "VERIFIED OK" in text and "ISSUES FOUND" in text
    # exactly the standard verify prompt — nothing appended
    assert "Language Server" not in text


async def test_provider_only_runs_in_verify_phase():
    calls: list = []

    async def provider(files):
        calls.append(files)
        return "SHOULD_NOT_APPEAR"

    mw = PEVMiddleware(diagnostics_provider=provider)
    for phase in ("plan", "execute", "fast"):
        captured = await _run_awrap(mw, _request(_state([HumanMessage("fix"), _edited("x.py")], phase=phase)))
        assert "SHOULD_NOT_APPEAR" not in _sys_text(captured)
    assert calls == []  # provider never invoked outside verify


async def test_provider_receives_edited_files():
    seen: list = []

    async def provider(files):
        seen.append(list(files))
        return None

    mw = PEVMiddleware(diagnostics_provider=provider)
    await _run_awrap(mw, _request(_state([HumanMessage("fix"), _edited("a.py", "b.py")])))
    assert seen == [["a.py", "b.py"]]


async def test_no_edited_files_skips_provider():
    calls: list = []

    async def provider(files):
        calls.append(1)
        return "X"

    mw = PEVMiddleware(diagnostics_provider=provider)
    captured = await _run_awrap(mw, _request(_state([HumanMessage("fix")])))  # no tool calls
    assert calls == []
    assert "X" not in _sys_text(captured)


async def test_provider_exception_degrades_gracefully():
    async def provider(files):
        raise RuntimeError("server exploded")

    mw = PEVMiddleware(diagnostics_provider=provider)
    captured = await _run_awrap(mw, _request(_state([HumanMessage("fix"), _edited("x.py")])))
    # no exception propagated; verify prompt intact, no addendum
    assert "VERIFIED OK" in _sys_text(captured)


# ── Markers / circuit breakers unchanged with a provider present ──────────────

def test_markers_and_breakers_unchanged_with_provider():
    mw = PEVMiddleware(diagnostics_provider=lambda files: None)
    rt = _runtime()

    # VERIFIED OK still ends
    s = _state([AIMessage(content="all good\nVERIFIED OK")])
    assert mw.after_model(s, rt)["jump_to"] == "end"

    # ISSUES FOUND still loops to execute and increments the error counter
    s2 = _state([AIMessage(content="ISSUES FOUND: nope")])
    r2 = mw.after_model(s2, rt)
    assert r2["_pev_phase"] == "execute"
    assert r2["_pev_error_count"] == 1
    assert r2["jump_to"] == "model"

    # circuit breaker still fires at max errors (does not get bypassed)
    s3 = _state([AIMessage(content="working")], phase="verify")
    s3["_pev_error_count"] = 3
    assert mw.after_model(s3, rt)["jump_to"] == "end"


# ── Provider side: verify_diagnostics_addendum (ERRORS-ONLY) ──────────────────

class _FakeClient:
    def __init__(self, diags):
        self._diags = diags
        self.is_running = True

    async def update_document(self, path, text):
        return None

    async def get_diagnostics(self, path, **kw):
        return self._diags


def _install_provider_client(monkeypatch, diags):
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": True)

    class _Mgr:
        async def get_client(self, language_id, root):
            return _FakeClient(diags)

    monkeypatch.setattr(lsp_tools, "_MANAGER", _Mgr())


def _err(line=2, msg="bad type", code="reportReturnType"):
    return Diagnostic(range=Range(Position(line, 0), Position(line, 3)),
                      message=msg, severity=DiagnosticSeverity.ERROR, code=code)


def _warn(line=1, msg="unused import"):
    return Diagnostic(range=Range(Position(line, 0), Position(line, 3)),
                      message=msg, severity=DiagnosticSeverity.WARNING)


async def test_provider_returns_addendum_on_errors(monkeypatch, tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def x() -> int: return 'no'\n", encoding="utf-8")
    _install_provider_client(monkeypatch, [_err(msg="not assignable to int")])
    out = await lsp_tools.verify_diagnostics_addendum([str(f)])
    assert out is not None
    assert "ISSUES FOUND" in out             # nudges the model to loop back
    assert "not assignable to int" in out
    assert "reportReturnType" in out


async def test_provider_warnings_only_returns_none(monkeypatch, tmp_path):
    """CRITICAL: warnings must NEVER trigger feedback (errors-only rule)."""
    f = tmp_path / "m.py"
    f.write_text("import os\n", encoding="utf-8")
    _install_provider_client(monkeypatch, [_warn(), _warn(line=2, msg="also unused")])
    assert await lsp_tools.verify_diagnostics_addendum([str(f)]) is None


async def test_provider_mixed_keeps_only_errors(monkeypatch, tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    _install_provider_client(monkeypatch, [_err(msg="REAL_ERROR"), _warn(msg="JUST_A_WARNING")])
    out = await lsp_tools.verify_diagnostics_addendum([str(f)])
    assert out is not None
    assert "REAL_ERROR" in out
    assert "JUST_A_WARNING" not in out


async def test_provider_clean_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    _install_provider_client(monkeypatch, [])
    assert await lsp_tools.verify_diagnostics_addendum([str(f)]) is None


async def test_provider_no_server_returns_none(monkeypatch, tmp_path):
    """Optional: with no language server, the provider is a no-op."""
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": False)
    assert await lsp_tools.verify_diagnostics_addendum([str(f)]) is None


async def test_provider_unsupported_filetype_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello world\n", encoding="utf-8")
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": True)
    assert await lsp_tools.verify_diagnostics_addendum([str(f)]) is None


async def test_provider_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": True)
    assert await lsp_tools.verify_diagnostics_addendum([str(tmp_path / "ghost.py")]) is None
