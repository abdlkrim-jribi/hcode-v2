"""Tests for the LSP-backed agent tools (W3.2).

All tests run against a **mocked** LSP client — no real pyright, no subprocess —
so they are deterministic and CI-safe regardless of whether a language server is
installed.  Two things are asserted throughout:

  * each tool returns the expected human-readable shape, and
  * the layer degrades gracefully (a clear message, never an exception) when no
    server is available, the file type is unsupported, the symbol is missing, or
    a query errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hcode_v2.tools import lsp_tools
from hcode_v2.lsp import LSPError
from hcode_v2.lsp.protocol import (
    Diagnostic,
    DiagnosticSeverity,
    HoverResult,
    Location,
    Position,
    Range,
    path_to_uri,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeClient:
    """A canned LSPClient: records syncs, returns preset query results."""

    def __init__(self, *, diagnostics=None, definitions=None, references=None,
                 hover=None, raise_on=None) -> None:
        self._diagnostics = diagnostics or []
        self._definitions = definitions or []
        self._references = references or []
        self._hover = hover
        self._raise_on = set(raise_on or ())
        self.synced: list[tuple[Path, str]] = []
        self.is_running = True

    async def update_document(self, path, text):
        self.synced.append((Path(path), text))

    async def open_document(self, path, text=None):
        return None

    async def get_diagnostics(self, path, **kw):
        if "get_diagnostics" in self._raise_on:
            raise LSPError("server crashed")
        return self._diagnostics

    async def goto_definition(self, path, line, col, **kw):
        if "goto_definition" in self._raise_on:
            raise LSPError("server crashed")
        return self._definitions

    async def find_references(self, path, line, col, **kw):
        return self._references

    async def hover(self, path, line, col, **kw):
        return self._hover


def _install_client(monkeypatch, client) -> None:
    """Force the tools to use *client*, independent of real pyright presence."""
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": True)

    class _FakeManager:
        async def get_client(self, language_id, root):
            return client

    monkeypatch.setattr(lsp_tools, "_MANAGER", _FakeManager())


def _py_file(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "mod.py"
    f.write_text(body, encoding="utf-8")
    return f


_SAMPLE_BODY = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "result = add(1, 2)\n"
)


# ── check_diagnostics ────────────────────────────────────────────────────────

async def test_check_diagnostics_formats_errors(monkeypatch, tmp_path):
    diag = Diagnostic(
        range=Range(Position(13, 11), Position(13, 25)),
        message='Type "str" is not assignable to return type "int"',
        severity=DiagnosticSeverity.ERROR, code="reportReturnType", source="Pyright",
    )
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(diagnostics=[diag]))

    out = await lsp_tools.check_diagnostics.ainvoke({"path": str(f)})
    assert "1 error(s)" in out
    assert "ERROR" in out
    assert "reportReturnType" in out
    assert ":14:12:" in out  # 0-based 13:11 reported 1-based
    assert "assignable" in out


async def test_check_diagnostics_clean_file(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(diagnostics=[]))
    out = await lsp_tools.check_diagnostics.ainvoke({"path": str(f)})
    assert "clean" in out.lower()


async def test_check_diagnostics_separates_warnings(monkeypatch, tmp_path):
    err = Diagnostic(range=Range(Position(0, 0), Position(0, 3)), message="bad",
                     severity=DiagnosticSeverity.ERROR)
    warn = Diagnostic(range=Range(Position(2, 0), Position(2, 3)), message="unused",
                      severity=DiagnosticSeverity.WARNING)
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(diagnostics=[err, warn]))
    out = await lsp_tools.check_diagnostics.ainvoke({"path": str(f)})
    assert "1 error(s)" in out and "1 warning(s)" in out


async def test_check_diagnostics_resyncs_disk_content(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    client = FakeClient(diagnostics=[])
    _install_client(monkeypatch, client)
    await lsp_tools.check_diagnostics.ainvoke({"path": str(f)})
    # the tool pushed the file's current content to the server before querying
    assert client.synced and client.synced[0][1] == _SAMPLE_BODY


# ── goto_definition / find_references ────────────────────────────────────────

async def test_goto_definition_formats_location(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    loc = Location(uri=path_to_uri(f), range=Range(Position(0, 4), Position(0, 7)))
    _install_client(monkeypatch, FakeClient(definitions=[loc]))
    out = await lsp_tools.goto_definition.ainvoke({"path": str(f), "symbol": "add"})
    assert "1 definition for 'add'" in out
    assert ":1:5" in out  # 0-based 0:4 -> 1-based 1:5


async def test_goto_definition_none_found(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(definitions=[]))
    out = await lsp_tools.goto_definition.ainvoke({"path": str(f), "symbol": "add"})
    assert "No definition found for 'add'" in out


async def test_find_references_pluralises(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    refs = [
        Location(uri=path_to_uri(f), range=Range(Position(0, 4), Position(0, 7))),
        Location(uri=path_to_uri(f), range=Range(Position(3, 9), Position(3, 12))),
    ]
    _install_client(monkeypatch, FakeClient(references=refs))
    out = await lsp_tools.find_references.ainvoke({"path": str(f), "symbol": "add"})
    assert "2 references for 'add'" in out
    assert ":1:5" in out and ":4:10" in out


async def test_symbol_not_found_is_a_clear_message(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient())
    out = await lsp_tools.goto_definition.ainvoke({"path": str(f), "symbol": "nonexistent"})
    assert "not found" in out and "nonexistent" in out


async def test_line_hint_targets_specific_occurrence(monkeypatch, tmp_path):
    body = "x = 1\nx = 2\nprint(x)\n"
    f = tmp_path / "m.py"
    f.write_text(body, encoding="utf-8")
    client = FakeClient(definitions=[
        Location(uri=path_to_uri(f), range=Range(Position(0, 0), Position(0, 1)))
    ])
    _install_client(monkeypatch, client)
    # line=2 should resolve the 'x' on line 2, not line 1 — just assert no error
    out = await lsp_tools.goto_definition.ainvoke({"path": str(f), "symbol": "x", "line": 2})
    assert "definition for 'x'" in out


# ── hover_info ───────────────────────────────────────────────────────────────

async def test_hover_info_returns_signature(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    hover = HoverResult(value="(function) def add(a: int, b: int) -> int", range=None)
    _install_client(monkeypatch, FakeClient(hover=hover))
    out = await lsp_tools.hover_info.ainvoke({"path": str(f), "symbol": "add"})
    assert "def add" in out


async def test_hover_info_none(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(hover=None))
    out = await lsp_tools.hover_info.ainvoke({"path": str(f), "symbol": "add"})
    assert "No hover information" in out


# ── Graceful degradation ─────────────────────────────────────────────────────

_ALL_TOOLS = [
    ("check_diagnostics", {"path": "PATH"}),
    ("goto_definition", {"path": "PATH", "symbol": "add"}),
    ("find_references", {"path": "PATH", "symbol": "add"}),
    ("hover_info", {"path": "PATH", "symbol": "add"}),
]


@pytest.mark.parametrize("tool_name,args", _ALL_TOOLS)
async def test_tools_degrade_when_no_server_installed(monkeypatch, tmp_path, tool_name, args):
    """With no language server, every tool returns a clear note, not an error."""
    f = _py_file(tmp_path, _SAMPLE_BODY)
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": False)
    args = {**args, "path": str(f)}
    out = await getattr(lsp_tools, tool_name).ainvoke(args)
    assert "not available" in out.lower()
    assert "skipped" in out.lower()


@pytest.mark.parametrize("tool_name,args", _ALL_TOOLS)
async def test_tools_handle_unsupported_file_type(monkeypatch, tmp_path, tool_name, args):
    """A file with no registered server degrades cleanly (no crash)."""
    f = tmp_path / "notes.txt"
    f.write_text("hello world add\n", encoding="utf-8")
    # lsp_available is irrelevant — config_for_path returns None for .txt first.
    args = {**args, "path": str(f)}
    out = await getattr(lsp_tools, tool_name).ainvoke(args)
    assert "No language server is configured" in out


@pytest.mark.parametrize("tool_name,args", _ALL_TOOLS)
async def test_tools_handle_missing_file(monkeypatch, tmp_path, tool_name, args):
    _install_client(monkeypatch, FakeClient())
    args = {**args, "path": str(tmp_path / "does_not_exist.py")}
    out = await getattr(lsp_tools, tool_name).ainvoke(args)
    assert "file not found" in out.lower()


async def test_check_diagnostics_catches_lsp_error(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(raise_on={"get_diagnostics"}))
    out = await lsp_tools.check_diagnostics.ainvoke({"path": str(f)})
    assert "Language server error" in out


async def test_goto_definition_catches_lsp_error(monkeypatch, tmp_path):
    f = _py_file(tmp_path, _SAMPLE_BODY)
    _install_client(monkeypatch, FakeClient(raise_on={"goto_definition"}))
    out = await lsp_tools.goto_definition.ainvoke({"path": str(f), "symbol": "add"})
    assert "Language server error" in out


# ── Manager: reuse one client, degrade to None ───────────────────────────────

async def test_manager_returns_none_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": False)
    mgr = lsp_tools._LSPClientManager()
    assert await mgr.get_client("python", tmp_path) is None


async def test_manager_reuses_one_client_per_workspace(monkeypatch, tmp_path):
    """Don't spawn pyright per call — the manager starts one client and reuses it."""
    starts: list[int] = []

    class _FakeLSP:
        def __init__(self, cfg, root):
            self.is_running = True

        async def start(self, **kw):
            starts.append(1)

        async def stop(self, **kw):
            self.is_running = False

    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": True)
    monkeypatch.setattr(lsp_tools, "LSPClient", _FakeLSP)

    mgr = lsp_tools._LSPClientManager()
    c1 = await mgr.get_client("python", tmp_path)
    c2 = await mgr.get_client("python", tmp_path)
    assert c1 is c2
    assert len(starts) == 1  # started exactly once, then reused


async def test_manager_rebuilds_when_client_dies(monkeypatch, tmp_path):
    starts: list[int] = []

    class _FakeLSP:
        def __init__(self, cfg, root):
            self.is_running = True

        async def start(self, **kw):
            starts.append(1)

        async def stop(self, **kw):
            self.is_running = False

    monkeypatch.setattr(lsp_tools, "lsp_available", lambda language_id="python": True)
    monkeypatch.setattr(lsp_tools, "LSPClient", _FakeLSP)

    mgr = lsp_tools._LSPClientManager()
    c1 = await mgr.get_client("python", tmp_path)
    c1.is_running = False  # simulate the server dying
    c2 = await mgr.get_client("python", tmp_path)
    assert c1 is not c2
    assert len(starts) == 2
