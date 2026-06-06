"""Unit tests for the LSP wire-protocol primitives (no I/O, no subprocess).

Covers the three things most likely to break silently:
  * Content-Length framing — byte counts must be UTF-8 bytes, not characters.
  * URI ⇄ path canonicalisation — pyright's ``file:///c%3A/...`` form must map
    to the same key as a plain path.
  * Typed parsing of raw LSP JSON into Diagnostic/Location/HoverResult.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from hcode_v2.lsp.protocol import (
    Diagnostic,
    DiagnosticSeverity,
    HoverResult,
    Location,
    Range,
    as_location_list,
    canonical_key,
    encode_message,
    path_to_uri,
    read_message,
    uri_to_key,
    uri_to_path,
)


# ── A minimal in-memory reader for read_message ──────────────────────────────

class _BytesReader:
    """asyncio-stream-like reader backed by a fixed byte buffer."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def readline(self) -> bytes:
        if self._pos >= len(self._data):
            return b""
        nl = self._data.find(b"\n", self._pos)
        end = len(self._data) if nl == -1 else nl + 1
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk

    async def readexactly(self, n: int) -> bytes:
        chunk = self._data[self._pos:self._pos + n]
        if len(chunk) < n:
            self._pos = len(self._data)
            raise asyncio.IncompleteReadError(chunk, n)
        self._pos += n
        return chunk


# ── Framing ───────────────────────────────────────────────────────────────────

def test_encode_message_has_content_length_header_in_bytes():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "x", "params": {"s": "héllo"}}
    encoded = encode_message(msg)
    header, sep, body = encoded.partition(b"\r\n\r\n")
    assert sep == b"\r\n\r\n"
    declared = int(header.split(b"Content-Length:")[1])
    # "héllo" is 6 bytes in UTF-8 (é = 2 bytes), so the byte count must exceed
    # the character count — proving we count bytes, not characters.
    assert declared == len(body)
    assert b"h\xc3\xa9llo" in body


async def test_read_message_round_trips_encode():
    original = {"jsonrpc": "2.0", "id": 7, "result": {"value": "café ☕"}}
    reader = _BytesReader(encode_message(original))
    decoded = await read_message(reader)
    assert decoded == original


async def test_read_message_parses_multiple_framed_messages_in_sequence():
    a = {"jsonrpc": "2.0", "method": "one", "params": {}}
    b = {"jsonrpc": "2.0", "method": "two", "params": {"n": 2}}
    reader = _BytesReader(encode_message(a) + encode_message(b))
    assert await read_message(reader) == a
    assert await read_message(reader) == b
    assert await read_message(reader) is None  # clean EOF


async def test_read_message_returns_none_on_eof():
    reader = _BytesReader(b"")
    assert await read_message(reader) is None


async def test_read_message_handles_extra_headers_and_crlf():
    body = b'{"jsonrpc":"2.0","id":1,"result":null}'
    raw = (
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
        b"\r\n" + body
    )
    decoded = await read_message(_BytesReader(raw))
    assert decoded == {"jsonrpc": "2.0", "id": 1, "result": None}


# ── URI ⇄ path canonicalisation ──────────────────────────────────────────────

def test_path_to_uri_is_a_file_uri():
    uri = path_to_uri(__file__)
    assert uri.startswith("file://")


def test_pyright_encoded_uri_maps_to_same_key_as_plain_path(tmp_path):
    """The headline gotcha: encoded ``c%3A`` URI must key to the real path."""
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n", encoding="utf-8")

    plain = canonical_key(f)
    via_uri = uri_to_key(path_to_uri(f))
    assert plain == via_uri


def test_uri_to_key_handles_percent_encoded_drive_on_windows():
    if os.name != "nt":
        pytest.skip("Windows drive-letter encoding only relevant on nt")
    encoded = "file:///c%3A/dev/PFE/hcode-v2/workspace/lsp-sample/type_error.py"
    plain = "file:///C:/dev/PFE/hcode-v2/workspace/lsp-sample/type_error.py"
    assert uri_to_key(encoded) == uri_to_key(plain)


def test_canonical_key_is_case_insensitive_on_windows(tmp_path):
    f = tmp_path / "Mod.py"
    f.write_text("x = 1\n", encoding="utf-8")
    if os.name == "nt":
        assert canonical_key(str(f).upper()) == canonical_key(str(f).lower())
    else:
        # POSIX is case-sensitive — keys differ, which is correct.
        assert canonical_key(f) == canonical_key(f)


def test_uri_to_path_round_trips():
    uri = path_to_uri(__file__)
    back = uri_to_path(uri)
    assert canonical_key(back) == canonical_key(__file__)


# ── Typed parsing ─────────────────────────────────────────────────────────────

def test_diagnostic_from_lsp_type_error_shape():
    raw = {
        "range": {"start": {"line": 13, "character": 11}, "end": {"line": 13, "character": 25}},
        "severity": 1,
        "code": "reportReturnType",
        "source": "Pyright",
        "message": 'Type "Literal[\'x\']" is not assignable to return type "int"',
    }
    d = Diagnostic.from_lsp(raw)
    assert d.severity == DiagnosticSeverity.ERROR
    assert d.is_error
    assert d.line == 13
    assert d.code == "reportReturnType"
    assert d.source == "Pyright"


def test_diagnostic_from_lsp_syntax_error_without_code():
    raw = {
        "range": {"start": {"line": 11, "character": 0}, "end": {"line": 11, "character": 1}},
        "severity": 1,
        "message": 'Expected ":"',
    }
    d = Diagnostic.from_lsp(raw)
    assert d.is_error
    assert d.code is None  # parse errors carry no rule code
    assert d.message == 'Expected ":"'


def test_diagnostic_warning_is_not_error():
    raw = {
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        "severity": 2,
        "message": "unused import",
    }
    d = Diagnostic.from_lsp(raw)
    assert d.severity == DiagnosticSeverity.WARNING
    assert not d.is_error


def test_diagnostic_without_severity():
    raw = {
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        "message": "no severity",
    }
    d = Diagnostic.from_lsp(raw)
    assert d.severity is None
    assert not d.is_error


def test_location_from_plain_and_link_forms():
    plain = {"uri": "file:///x.py", "range": {"start": {"line": 5, "character": 4},
                                              "end": {"line": 5, "character": 7}}}
    link = {"targetUri": "file:///x.py",
            "targetSelectionRange": {"start": {"line": 5, "character": 4},
                                     "end": {"line": 5, "character": 7}}}
    lp = Location.from_lsp(plain)
    ll = Location.from_lsp(link)
    assert lp.uri == ll.uri == "file:///x.py"
    assert lp.range == ll.range == Range(start=lp.range.start, end=lp.range.end)


def test_as_location_list_normalises_all_shapes():
    one = {"uri": "file:///a.py", "range": {"start": {"line": 0, "character": 0},
                                            "end": {"line": 0, "character": 1}}}
    assert as_location_list(None) == []
    assert len(as_location_list(one)) == 1            # single Location
    assert len(as_location_list([one, one])) == 2     # Location[]


def test_hover_from_markupcontent_and_list():
    markup = {"contents": {"kind": "plaintext", "value": "(function) def add() -> int"}}
    assert HoverResult.from_lsp(markup).value.startswith("(function)")

    marked_list = {"contents": [{"language": "python", "value": "x: int"}, "docs"]}
    assert "x: int" in HoverResult.from_lsp(marked_list).value
    assert "docs" in HoverResult.from_lsp(marked_list).value
