"""LSP wire protocol primitives — framing, URI⇄path, and typed results.

This module is deliberately free of any I/O or subprocess concern so it can be
unit-tested in isolation.  It holds three things:

1. **Content-Length framing** (`encode_message` / `read_message`).  LSP messages
   are NOT bare JSON lines — each carries HTTP-style headers terminated by a
   blank ``\\r\\n`` line, then a UTF-8 JSON body whose length in *bytes* is given
   by the ``Content-Length`` header.  Get this wrong and the stream desyncs.

2. **URI ⇄ path normalisation** (`path_to_uri` / `uri_to_path` / `canonical_key`).
   pyright pushes diagnostics keyed by URIs like ``file:///c%3A/dev/...`` —
   percent-encoded drive colon, lower-cased drive — which does NOT string-match a
   naive ``Path.as_uri()`` of ``file:///C:/dev/...``.  Everything the client stores
   or looks up is keyed by `canonical_key`, the resolved filesystem path
   (case-folded on Windows), so the encoding form never matters.

3. **Typed result objects** parsed from raw LSP JSON (`Diagnostic`, `Location`,
   `HoverResult`, …) so callers don't poke at dicts.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Optional, Protocol


# ── Content-Length framing ────────────────────────────────────────────────────

class _Readable(Protocol):
    async def readline(self) -> bytes: ...
    async def readexactly(self, n: int) -> bytes: ...


def encode_message(obj: dict) -> bytes:
    """Serialise a JSON-RPC object into a Content-Length-framed byte string.

    The header length is the body length in **bytes** (UTF-8), per the LSP spec.
    """
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


async def read_message(reader: _Readable) -> Optional[dict]:
    """Read exactly one framed LSP message from *reader*.

    Returns the decoded JSON object, or ``None`` on clean EOF (the only way to
    tell the stream ended vs. a blank header-terminator line is that EOF yields
    an empty ``b""`` from ``readline`` whereas the separator yields ``b"\\r\\n"``).
    Raises ``asyncio.IncompleteReadError`` if EOF lands mid-body.
    """
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line == b"":
            return None  # EOF
        stripped = line.rstrip(b"\r\n")
        if stripped == b"":
            break  # blank line → end of headers
        key, _, value = stripped.partition(b":")
        headers[key.decode("ascii").strip().lower()] = value.decode("ascii").strip()

    length = int(headers.get("content-length", 0))
    if length == 0:
        return {}
    body = await reader.readexactly(length)
    return json.loads(body.decode("utf-8"))


# ── URI ⇄ path normalisation ──────────────────────────────────────────────────

_WIN_DRIVE_RE = re.compile(r"^/[A-Za-z]:")


def path_to_uri(path: os.PathLike | str) -> str:
    """Absolute filesystem path → ``file://`` URI (via `Path.as_uri`)."""
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    """``file://`` URI → filesystem `Path`, undoing percent-encoding.

    Handles pyright's ``file:///c%3A/...`` form (encoded drive colon) as well as
    the plain ``file:///C:/...`` form, and POSIX ``file:///home/...``.
    """
    parsed = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(parsed.path)
    if os.name == "nt" and _WIN_DRIVE_RE.match(path):
        path = path[1:]  # strip leading slash before drive letter: /C:/x → C:/x
    return Path(path)


def canonical_key(path: os.PathLike | str) -> str:
    """Canonical, comparable key for a path — resolved, case-folded on Windows.

    This is what the client keys *all* per-document state on (diagnostics,
    versions, settle-events) so that the URI a server echoes back always maps to
    the same bucket as the path a caller passes in.
    """
    resolved = str(Path(path).resolve())
    return resolved.lower() if os.name == "nt" else resolved


def uri_to_key(uri: str) -> str:
    """Convenience: a server-sent URI straight to its `canonical_key`."""
    return canonical_key(uri_to_path(uri))


# ── Typed result objects ──────────────────────────────────────────────────────

class DiagnosticSeverity(IntEnum):
    """LSP diagnostic severities (1 = most severe)."""
    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


@dataclass(frozen=True)
class Position:
    """0-based ``{line, character}``; ``character`` is a UTF-16 code-unit offset."""
    line: int
    character: int

    @classmethod
    def from_lsp(cls, d: dict) -> "Position":
        return cls(line=d["line"], character=d["character"])


@dataclass(frozen=True)
class Range:
    """Half-open ``{start, end}`` range."""
    start: Position
    end: Position

    @classmethod
    def from_lsp(cls, d: dict) -> "Range":
        return cls(start=Position.from_lsp(d["start"]), end=Position.from_lsp(d["end"]))


@dataclass(frozen=True)
class Diagnostic:
    """A single diagnostic (error/warning/…) for a document."""
    range: Range
    message: str
    severity: Optional[DiagnosticSeverity] = None
    code: Optional[str | int] = None
    source: Optional[str] = None

    @classmethod
    def from_lsp(cls, d: dict) -> "Diagnostic":
        sev = d.get("severity")
        return cls(
            range=Range.from_lsp(d["range"]),
            message=d.get("message", ""),
            severity=DiagnosticSeverity(sev) if sev in (1, 2, 3, 4) else None,
            code=d.get("code"),
            source=d.get("source"),
        )

    @property
    def is_error(self) -> bool:
        return self.severity == DiagnosticSeverity.ERROR

    @property
    def line(self) -> int:
        """0-based start line — convenience for callers."""
        return self.range.start.line


@dataclass(frozen=True)
class Location:
    """A ``{uri, range}`` location (where a symbol is defined / referenced)."""
    uri: str
    range: Range

    @classmethod
    def from_lsp(cls, d: dict) -> "Location":
        # Accept both Location (uri/range) and LocationLink (targetUri/targetRange).
        uri = d.get("uri") or d.get("targetUri", "")
        rng = d.get("range") or d.get("targetSelectionRange") or d.get("targetRange")
        return cls(uri=uri, range=Range.from_lsp(rng))

    @property
    def path(self) -> Path:
        return uri_to_path(self.uri)


@dataclass(frozen=True)
class HoverResult:
    """Hover text at a position (type / signature / docs)."""
    value: str
    range: Optional[Range] = None

    @classmethod
    def from_lsp(cls, d: dict) -> "HoverResult":
        contents = d.get("contents")
        rng = Range.from_lsp(d["range"]) if d.get("range") else None
        return cls(value=_hover_text(contents), range=rng)


def _hover_text(contents: Any) -> str:
    """Flatten LSP ``Hover.contents`` (MarkupContent | MarkedString | list) to text."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        # MarkupContent {kind, value} or MarkedString {language, value}
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        return "\n".join(_hover_text(c) for c in contents)
    return str(contents)


def as_location_list(result: Any) -> list[Location]:
    """Normalise a definition/references result (``Location | Location[] | null``)."""
    if result is None:
        return []
    if isinstance(result, dict):
        return [Location.from_lsp(result)]
    if isinstance(result, list):
        return [Location.from_lsp(x) for x in result]
    return []
