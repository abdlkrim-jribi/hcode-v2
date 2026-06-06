"""Tests for the LSP client.

Two layers, mirroring the streaming-bridge test style:

  1. Unit — drive `LSPClient` against an in-memory **fake server** (no real
     subprocess).  A duplex pair of `asyncio.StreamReader`s carries
     Content-Length-framed messages both ways, so the client's real framing,
     id-correlation, pushed-diagnostics capture, and lifecycle are exercised
     end-to-end without pyright.

  2. Integration — drive `LSPClient` against the real ``pyright-langserver``
     over the ``workspace/lsp-sample/`` fixture.  Marked ``integration`` and
     SKIPPED when pyright (or the fixture) is absent, so CI stays deterministic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hcode_v2.lsp import (
    DiagnosticSeverity,
    LSPClient,
    LSPUnavailable,
    get_config,
    lsp_available,
)
from hcode_v2.lsp.config import LanguageServerConfig
from hcode_v2.lsp.protocol import encode_message, path_to_uri, read_message


# ── In-memory duplex transport + fake server ─────────────────────────────────

class _WriterAdapter:
    """Minimal StreamWriter-like façade feeding a paired StreamReader."""

    def __init__(self, target: asyncio.StreamReader) -> None:
        self._target = target

    def write(self, data: bytes) -> None:
        self._target.feed_data(data)

    async def drain(self) -> None:  # nothing to flush for an in-memory pipe
        return None

    def close(self) -> None:
        self._target.feed_eof()


def _make_duplex():
    """Return (client_reader, client_writer, server_reader, server_writer)."""
    c2s = asyncio.StreamReader()  # client → server
    s2c = asyncio.StreamReader()  # server → client
    return s2c, _WriterAdapter(c2s), c2s, _WriterAdapter(s2c)


class FakeProc:
    """Stand-in for asyncio.subprocess.Process used by LSPClient.stop()."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9
        self._exited.set()

    def exit(self, code: int = 0) -> None:
        if self.returncode is None:
            self.returncode = code
        self._exited.set()


class FakeLSPServer:
    """A canned language server: enough of the protocol to drive the client."""

    def __init__(self, server_reader, server_writer, client_reader_target, proc) -> None:
        self._reader = server_reader
        self._writer = server_writer
        self._client_target = client_reader_target  # StreamReader the client reads
        self._proc = proc
        self.received: list[dict] = []
        self.opened_uris: list[str] = []
        # Optional diagnostics to push on didOpen, keyed by filename substring.
        self.diagnostics_on_open: list[dict] = []
        self.emit_progress = False

    def _send(self, obj: dict) -> None:
        self._writer.write(encode_message(obj))

    async def run(self) -> None:
        while True:
            try:
                msg = await read_message(self._reader)
            except asyncio.IncompleteReadError:
                return
            if msg is None:
                return
            self.received.append(msg)
            method = msg.get("method")
            mid = msg.get("id")

            if method == "initialize":
                self._send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {
                        "capabilities": {"hoverProvider": True, "definitionProvider": True},
                        "serverInfo": {"name": "fake-lsp", "version": "0.0.1"},
                    },
                })
            elif method == "initialized":
                pass
            elif method == "textDocument/didOpen":
                uri = msg["params"]["textDocument"]["uri"]
                self.opened_uris.append(uri)
                if self.emit_progress:
                    self._send({"jsonrpc": "2.0", "method": "pyright/beginProgress"})
                # initial empty push (as pyright does)
                self._send({
                    "jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                    "params": {"uri": uri, "diagnostics": []},
                })
                if self.diagnostics_on_open:
                    self._send({
                        "jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                        "params": {"uri": uri, "diagnostics": self.diagnostics_on_open},
                    })
                if self.emit_progress:
                    self._send({"jsonrpc": "2.0", "method": "pyright/endProgress"})
            elif method == "textDocument/hover":
                self._send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {"contents": {"kind": "plaintext",
                                            "value": "(function) def add(a: int, b: int) -> int"}},
                })
            elif method == "textDocument/definition":
                uri = msg["params"]["textDocument"]["uri"]
                self._send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": [{"uri": uri, "range": {"start": {"line": 5, "character": 4},
                                                      "end": {"line": 5, "character": 7}}}],
                })
            elif method == "textDocument/references":
                uri = msg["params"]["textDocument"]["uri"]
                self._send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": [
                        {"uri": uri, "range": {"start": {"line": 5, "character": 4},
                                               "end": {"line": 5, "character": 7}}},
                        {"uri": uri, "range": {"start": {"line": 22, "character": 6},
                                               "end": {"line": 22, "character": 9}}},
                    ],
                })
            elif method == "shutdown":
                self._send({"jsonrpc": "2.0", "id": mid, "result": None})
            elif method == "exit":
                self._proc.exit(0)
                self._client_target.feed_eof()
                return


async def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    """Poll *predicate* (cooperatively yielding) until true or timed out."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


async def _make_client_with_fake_server(tmp_path, **server_attrs):
    """Wire an LSPClient to an in-memory FakeLSPServer and start it."""
    client_reader, client_writer, server_reader, server_writer = _make_duplex()
    proc = FakeProc()
    server = FakeLSPServer(server_reader, server_writer, client_reader, proc)
    for k, v in server_attrs.items():
        setattr(server, k, v)
    server_task = asyncio.create_task(server.run())

    client = LSPClient(get_config("python"), tmp_path)

    async def fake_spawn():
        client._reader = client_reader
        client._writer = client_writer
        client._proc = proc  # type: ignore[assignment]

    client._spawn = fake_spawn  # type: ignore[method-assign]
    await client.start(timeout=5)
    return client, server, server_task


# ── Unit: lifecycle / handshake ──────────────────────────────────────────────

async def test_start_runs_initialize_handshake(tmp_path):
    client, server, task = await _make_client_with_fake_server(tmp_path)
    try:
        assert client.is_running
        # the `initialized` notification is sent right after start(); give the
        # fake server a turn to consume it before asserting on what it received.
        await _wait_until(lambda: "initialized" in [m.get("method") for m in server.received])
        methods = [m.get("method") for m in server.received]
        assert "initialize" in methods
        assert "initialized" in methods
        # the client recorded the server's advertised capabilities
        assert client.server_capabilities.get("hoverProvider") is True
    finally:
        await client.stop()
        await task


async def test_stop_sends_shutdown_then_exit(tmp_path):
    client, server, task = await _make_client_with_fake_server(tmp_path)
    await client.stop()
    await task
    methods = [m.get("method") for m in server.received]
    assert "shutdown" in methods
    assert methods.index("shutdown") < methods.index("exit")
    assert not client.is_running


async def test_context_manager_starts_and_stops(tmp_path):
    client_reader, client_writer, server_reader, server_writer = _make_duplex()
    proc = FakeProc()
    server = FakeLSPServer(server_reader, server_writer, client_reader, proc)
    task = asyncio.create_task(server.run())
    client = LSPClient(get_config("python"), tmp_path)

    async def fake_spawn():
        client._reader, client._writer, client._proc = client_reader, client_writer, proc  # type: ignore[assignment]

    client._spawn = fake_spawn  # type: ignore[method-assign]
    async with client as c:
        assert c.is_running
    assert not client.is_running
    await task


# ── Unit: pushed diagnostics ─────────────────────────────────────────────────

async def test_pushed_diagnostics_are_captured(tmp_path):
    diag = {
        "range": {"start": {"line": 13, "character": 11}, "end": {"line": 13, "character": 25}},
        "severity": 1, "code": "reportReturnType", "source": "Pyright",
        "message": 'Type "str" is not assignable to return type "int"',
    }
    f = tmp_path / "type_error.py"
    f.write_text("def get() -> int:\n    return 'x'\n", encoding="utf-8")

    client, server, task = await _make_client_with_fake_server(
        tmp_path, diagnostics_on_open=[diag],
    )
    try:
        diags = await client.get_diagnostics(f, settle=0.05, timeout=2.0)
        assert len(diags) == 1
        assert diags[0].is_error
        assert diags[0].severity == DiagnosticSeverity.ERROR
        assert diags[0].code == "reportReturnType"
        assert diags[0].line == 13
    finally:
        await client.stop()
        await task


async def test_clean_file_has_no_diagnostics(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("x: int = 1\n", encoding="utf-8")
    client, server, task = await _make_client_with_fake_server(tmp_path)  # no diags
    try:
        diags = await client.get_diagnostics(f, settle=0.05, timeout=2.0)
        assert diags == []
    finally:
        await client.stop()
        await task


async def test_diagnostics_settle_waits_through_progress(tmp_path):
    """With progress notifications, the real (non-empty) push wins over the
    initial empty push even though both arrive for the same document."""
    diag = {
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        "severity": 1, "message": "boom",
    }
    f = tmp_path / "buggy.py"
    f.write_text("boom\n", encoding="utf-8")
    client, server, task = await _make_client_with_fake_server(
        tmp_path, diagnostics_on_open=[diag], emit_progress=True,
    )
    try:
        diags = await client.get_diagnostics(f, settle=0.05, timeout=2.0)
        assert len(diags) == 1
        assert diags[0].message == "boom"
    finally:
        await client.stop()
        await task


async def test_on_diagnostics_callback_fires(tmp_path):
    captured: list = []
    diag = {"range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 1}},
            "severity": 2, "message": "warn"}
    f = tmp_path / "w.py"
    f.write_text("import os\n", encoding="utf-8")

    client_reader, client_writer, server_reader, server_writer = _make_duplex()
    proc = FakeProc()
    server = FakeLSPServer(server_reader, server_writer, client_reader, proc)
    server.diagnostics_on_open = [diag]
    task = asyncio.create_task(server.run())
    client = LSPClient(get_config("python"), tmp_path,
                       on_diagnostics=lambda uri, ds: captured.append((uri, ds)))

    async def fake_spawn():
        client._reader, client._writer, client._proc = client_reader, client_writer, proc  # type: ignore[assignment]

    client._spawn = fake_spawn  # type: ignore[method-assign]
    await client.start(timeout=5)
    try:
        await client.get_diagnostics(f, settle=0.05, timeout=2.0)
        assert captured, "on_diagnostics never fired"
        assert any(len(ds) == 1 for _uri, ds in captured)
    finally:
        await client.stop()
        await task


# ── Unit: id-correlated requests ─────────────────────────────────────────────

async def test_definition_round_trips_by_id(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    client, server, task = await _make_client_with_fake_server(tmp_path)
    try:
        locs = await client.goto_definition(f, line=5, character=5)
        assert len(locs) == 1
        assert locs[0].range.start.line == 5
        # the server saw a definition request that carried an id
        defn = next(m for m in server.received if m.get("method") == "textDocument/definition")
        assert "id" in defn
    finally:
        await client.stop()
        await task


async def test_references_round_trips(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    client, server, task = await _make_client_with_fake_server(tmp_path)
    try:
        refs = await client.find_references(f, line=5, character=5)
        assert len(refs) == 2
    finally:
        await client.stop()
        await task


async def test_hover_round_trips(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    client, server, task = await _make_client_with_fake_server(tmp_path)
    try:
        hov = await client.hover(f, line=5, character=4)
        assert hov is not None
        assert "def add" in hov.value
    finally:
        await client.stop()
        await task


async def test_concurrent_requests_correlate_independently(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    client, server, task = await _make_client_with_fake_server(tmp_path)
    try:
        hov, locs, refs = await asyncio.gather(
            client.hover(f, 5, 4),
            client.goto_definition(f, 5, 5),
            client.find_references(f, 5, 5),
        )
        assert hov is not None and len(locs) == 1 and len(refs) == 2
    finally:
        await client.stop()
        await task


# ── Unit: graceful degradation ───────────────────────────────────────────────

async def test_start_raises_lspunavailable_when_binary_missing(tmp_path):
    bogus = LanguageServerConfig(language_id="python",
                                 command="hcode-no-such-langserver-xyz", args=())
    client = LSPClient(bogus, tmp_path)
    with pytest.raises(LSPUnavailable):
        await client.start()
    assert not client.is_running


def test_for_language_returns_none_for_unknown_language(tmp_path):
    assert LSPClient.for_language("klingon", tmp_path) is None


def test_lsp_available_false_for_unknown_language():
    assert lsp_available("klingon") is False


async def test_stop_is_idempotent_and_safe_before_start(tmp_path):
    bogus = LanguageServerConfig(language_id="python", command="nope-xyz", args=())
    client = LSPClient(bogus, tmp_path)
    await client.stop()  # never started — must not raise
    await client.stop()  # twice — must not raise


# ── Integration: real pyright over the lsp-sample fixture ────────────────────

_SAMPLE = Path(__file__).resolve().parent.parent / "workspace" / "lsp-sample"
_pyright_ready = lsp_available("python") and _SAMPLE.is_dir()
_skip = pytest.mark.skipif(
    not _pyright_ready,
    reason="pyright-langserver not installed or workspace/lsp-sample missing",
)


@pytest.mark.integration
@_skip
async def test_integration_clean_file_reports_no_errors():
    async with LSPClient(get_config("python"), _SAMPLE) as client:
        diags = await client.get_diagnostics(_SAMPLE / "clean.py", settle=0.6, timeout=20)
        assert [d for d in diags if d.is_error] == []


@pytest.mark.integration
@_skip
async def test_integration_type_error_is_detected():
    async with LSPClient(get_config("python"), _SAMPLE) as client:
        diags = await client.get_diagnostics(_SAMPLE / "type_error.py", settle=0.6, timeout=20)
        errors = [d for d in diags if d.is_error]
        assert errors, f"expected a type error, got {diags}"
        assert any("int" in d.message for d in errors)


@pytest.mark.integration
@_skip
async def test_integration_syntax_error_does_not_crash_client():
    async with LSPClient(get_config("python"), _SAMPLE) as client:
        diags = await client.get_diagnostics(_SAMPLE / "syntax_error.py", settle=0.6, timeout=20)
        assert [d for d in diags if d.is_error], f"expected a parse error, got {diags}"
        # client still usable after a broken file
        assert client.is_running


@pytest.mark.integration
@_skip
async def test_integration_hover_and_definition_round_trip():
    async with LSPClient(get_config("python"), _SAMPLE) as client:
        clean = _SAMPLE / "clean.py"
        text = clean.read_text(encoding="utf-8").splitlines()
        def_line = next(i for i, ln in enumerate(text) if "def add(" in ln)
        col = text[def_line].index("add")
        hov = await client.hover(clean, def_line, col)
        assert hov is not None and "add" in hov.value
        locs = await client.goto_definition(clean, def_line, col)
        assert locs  # add is defined (points at itself / its def)
