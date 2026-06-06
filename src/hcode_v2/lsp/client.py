"""`LSPClient` — spawn a language server and ask it semantic questions.

This is the W3.1 deliverable: a small async client that owns one language-server
subprocess and exposes read-only semantic queries (diagnostics, definition,
references, hover).  It is **language-agnostic** — everything server-specific
lives in a `LanguageServerConfig`.

Design notes
------------
* **Two inbound paths.**  Replies to our requests are correlated by JSON-RPC
  ``id`` through `self._pending` futures.  But diagnostics are *pushed*
  (`textDocument/publishDiagnostics`, no id) — those are captured by the reader
  loop into `self._diagnostics`, keyed by `canonical_key` so the URI form the
  server echoes back (``file:///c%3A/…``) maps to the path a caller passes in.
* **Diagnostics are asynchronous.**  After ``didOpen`` a server typically sends an
  immediate empty push, then the real diagnostics once analysis finishes.
  `get_diagnostics` therefore *settles*: it waits for a quiet gap after the last
  push (server-agnostic, no reliance on pyright's progress notifications).
* **Optional by construction.**  If the server binary is missing or fails to
  start, `start` raises `LSPUnavailable` — a clear, catchable signal.  It never
  hangs (every request is timeout-bounded) and never lets a server crash take
  down the caller.  Pair with `config.lsp_available()` to check before starting.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .config import DEFAULT_LANGUAGE, LanguageServerConfig, get_config
from .protocol import (
    Diagnostic,
    HoverResult,
    Location,
    as_location_list,
    canonical_key,
    encode_message,
    path_to_uri,
    read_message,
    uri_to_key,
)

logger = logging.getLogger(__name__)


def _progress_kind(msg: dict) -> Optional[str]:
    """Extract ``value.kind`` from a standard ``$/progress`` notification."""
    value = (msg.get("params") or {}).get("value")
    return value.get("kind") if isinstance(value, dict) else None


# ── Exceptions ────────────────────────────────────────────────────────────────

class LSPError(RuntimeError):
    """A request failed, timed out, or the connection dropped."""


class LSPUnavailable(LSPError):
    """The language server is not installed or could not be started.

    Catch this to degrade gracefully — it is the ``start`` signal that the layer
    should be treated as absent (zero regression vs. having no LSP at all).
    """


# ── Writer protocol (real ``proc.stdin`` or an injected test double) ──────────

class _Writer:
    """Structural type: anything with ``write(bytes)`` + awaitable ``drain()``."""
    def write(self, data: bytes) -> None: ...  # pragma: no cover
    async def drain(self) -> None: ...         # pragma: no cover


class LSPClient:
    """One language-server subprocess, exposed as semantic queries.

    Typical use::

        async with LSPClient(get_config("python"), root) as client:
            diags = await client.get_diagnostics("foo.py")

    or check availability first and construct from a language id::

        client = LSPClient.for_language("python", root)
        if client is not None:
            await client.start()
    """

    def __init__(
        self,
        config: LanguageServerConfig,
        root_path: os.PathLike | str,
        *,
        on_diagnostics: Optional[Callable[[str, list[Diagnostic]], None]] = None,
    ) -> None:
        self._config = config
        self._root = Path(root_path).resolve()
        self._on_diagnostics = on_diagnostics

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[_Writer] = None
        self._read_task: Optional[asyncio.Task] = None

        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

        # Per-document state, all keyed by canonical_key(path).
        self._diagnostics: dict[str, list[Diagnostic]] = {}
        self._diag_events: dict[str, asyncio.Event] = {}
        self._versions: dict[str, int] = {}

        # Analysis-progress tracking (pyright/*Progress and standard $/progress).
        # Lets get_diagnostics wait for the server to finish analysing rather than
        # mistaking the initial empty push for the final result.  Servers that
        # report no progress simply leave this idle and fall back to debounce.
        self._analysis_active = 0
        self._idle_event = asyncio.Event()
        self._idle_event.set()

        self._server_capabilities: dict = {}
        self._started = False
        self._closed = False

    # ── Construction helpers ──────────────────────────────────────────────────

    @classmethod
    def for_language(
        cls,
        language_id: str,
        root_path: os.PathLike | str,
        **kwargs: Any,
    ) -> Optional["LSPClient"]:
        """Build a client for *language_id*, or ``None`` if it isn't registered.

        Does not check installation (that happens at `start`); use
        `config.lsp_available` to gate beforehand if you want a no-op path.
        """
        cfg = get_config(language_id)
        if cfg is None:
            return None
        return cls(cfg, root_path, **kwargs)

    @property
    def is_running(self) -> bool:
        return (
            self._started
            and not self._closed
            and self._proc is not None
            and self._proc.returncode is None
        )

    @property
    def server_capabilities(self) -> dict:
        return self._server_capabilities

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, *, timeout: float = 20.0) -> None:
        """Spawn the server and run the ``initialize`` handshake.

        Raises `LSPUnavailable` if the binary is missing or won't spawn, and
        `LSPError` if the handshake times out (the process is killed first so we
        never leak it).  Never hangs — the handshake is timeout-bounded.
        """
        if self._started:
            return
        await self._spawn()
        self._read_task = asyncio.create_task(self._read_loop())
        try:
            await self._initialize(timeout=timeout)
        except Exception:
            await self.stop()
            raise
        self._started = True

    async def _spawn(self) -> None:
        argv = self._config.resolve_argv()
        if argv is None:
            raise LSPUnavailable(
                f"language server {self._config.command!r} not found on PATH"
            )
        # Primary: launch the resolved binary directly.  Windows npm shims are
        # ``.cmd`` batch files; CreateProcess runs them directly here, but keep a
        # ``cmd /c`` fallback for setups where it doesn't.
        attempts: list[list[str]] = [argv]
        if sys.platform == "win32" and argv[0].lower().endswith((".cmd", ".bat")):
            attempts.append(["cmd", "/c", *argv])

        last_err: Optional[BaseException] = None
        for attempt in attempts:
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *attempt,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=str(self._root),
                )
                self._reader = self._proc.stdout
                self._writer = self._proc.stdin  # type: ignore[assignment]
                logger.debug("LSP server started: %s", attempt)
                return
            except (FileNotFoundError, OSError) as exc:
                last_err = exc
                logger.debug("LSP spawn attempt failed (%s): %s", attempt, exc)
        raise LSPUnavailable(
            f"failed to start language server {self._config.command!r}: {last_err}"
        ) from last_err

    async def _initialize(self, *, timeout: float) -> None:
        root_uri = path_to_uri(self._root)
        result = await self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "hcode-v2", "version": "2.0.0"},
                "rootUri": root_uri,
                "workspaceFolders": [{"uri": root_uri, "name": self._root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "workspace": {"workspaceFolders": True},
                    "textDocument": {
                        "synchronization": {"dynamicRegistration": False},
                        "publishDiagnostics": {"relatedInformation": True},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "definition": {"linkSupport": False},
                        "references": {},
                    },
                },
            },
            timeout=timeout,
        )
        self._server_capabilities = (result or {}).get("capabilities", {})
        self._notify("initialized", {})
        await self._drain()

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Gracefully ``shutdown``/``exit`` the server, then ensure it's gone.

        Idempotent and never raises — safe to call from error paths and twice.
        """
        if self._closed:
            return
        self._closed = True

        if self._proc is not None and self._proc.returncode is None and self._writer is not None:
            try:
                await asyncio.wait_for(self._request("shutdown", None), timeout=timeout)
            except (LSPError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                logger.debug("LSP shutdown request failed: %s", exc)
            try:
                self._notify("exit", None)
                await self._drain()
            except Exception as exc:  # noqa: BLE001
                logger.debug("LSP exit notify failed: %s", exc)

        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if self._proc is not None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.debug("LSP server did not exit; killing")
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

        self._fail_pending(LSPError("client stopped"))
        self._started = False

    async def __aenter__(self) -> "LSPClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    # ── Document synchronisation ──────────────────────────────────────────────

    async def open_document(
        self,
        path: os.PathLike | str,
        text: Optional[str] = None,
        *,
        language_id: Optional[str] = None,
    ) -> None:
        """Send ``textDocument/didOpen`` so the server begins analysing the file.

        *text* defaults to the file's on-disk content.  Safe to call again — it
        bumps the version and re-opens, which servers treat as a fresh analysis.
        """
        p = Path(path).resolve()
        key = canonical_key(p)
        if text is None:
            text = p.read_text(encoding="utf-8")
        version = self._versions.get(key, 0) + 1
        self._versions[key] = version
        self._diag_events.setdefault(key, asyncio.Event())
        # New content invalidates any prior diagnostics for this file.
        self._diagnostics.pop(key, None)
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path_to_uri(p),
                    "languageId": language_id or self._config.language_id,
                    "version": version,
                    "text": text,
                }
            },
        )
        await self._drain()

    async def update_document(self, path: os.PathLike | str, text: str) -> None:
        """Send a full-text ``textDocument/didChange`` (opens first if needed)."""
        p = Path(path).resolve()
        key = canonical_key(p)
        if key not in self._versions:
            await self.open_document(p, text)
            return
        version = self._versions[key] + 1
        self._versions[key] = version
        self._diagnostics.pop(key, None)
        self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": path_to_uri(p), "version": version},
                "contentChanges": [{"text": text}],  # full-document replace
            },
        )
        await self._drain()

    async def close_document(self, path: os.PathLike | str) -> None:
        p = Path(path).resolve()
        key = canonical_key(p)
        self._versions.pop(key, None)
        self._diagnostics.pop(key, None)
        self._notify(
            "textDocument/didClose",
            {"textDocument": {"uri": path_to_uri(p)}},
        )
        await self._drain()

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_diagnostics(
        self,
        path: os.PathLike | str,
        *,
        settle: float = 0.4,
        timeout: float = 10.0,
    ) -> list[Diagnostic]:
        """Return the current diagnostics for *path*, waiting for analysis.

        Opens the document if it isn't already open, then *settles*: returns once
        no new ``publishDiagnostics`` has arrived for *settle* seconds (so the
        initial empty push isn't mistaken for the final result), bounded by
        *timeout*.  Returns ``[]`` for a clean file or if nothing ever arrives.
        """
        p = Path(path).resolve()
        key = canonical_key(p)
        if key not in self._versions:
            await self.open_document(p)
        event = self._diag_events.setdefault(key, asyncio.Event())

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        # Phase 1 — if the server announces analysis progress, wait for it to go
        # idle (all diagnostics for this pass published).  Bounded by the
        # deadline; servers that never report progress fall straight through.
        while loop.time() < deadline and self._analysis_active > 0:
            try:
                await asyncio.wait_for(self._idle_event.wait(), timeout=deadline - loop.time())
            except asyncio.TimeoutError:
                break

        # Phase 2 — debounce: return once no newer push arrives within `settle`.
        while True:
            now = loop.time()
            if now >= deadline:
                break
            if key in self._diagnostics:
                # Have a result; wait `settle` for a newer push, else we're done.
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=min(settle, deadline - now))
                    continue  # a newer push arrived → debounce again
                except asyncio.TimeoutError:
                    break  # quiet → settled
            else:
                # Nothing yet; wait for the first push up to the deadline.
                try:
                    await asyncio.wait_for(event.wait(), timeout=deadline - now)
                except asyncio.TimeoutError:
                    break
        return list(self._diagnostics.get(key, []))

    async def goto_definition(
        self, path: os.PathLike | str, line: int, character: int, *, timeout: float = 10.0
    ) -> list[Location]:
        """``textDocument/definition`` — where the symbol at (line, char) is defined."""
        result = await self._position_request("textDocument/definition", path, line, character, timeout)
        return as_location_list(result)

    async def find_references(
        self,
        path: os.PathLike | str,
        line: int,
        character: int,
        *,
        include_declaration: bool = True,
        timeout: float = 10.0,
    ) -> list[Location]:
        """``textDocument/references`` — everywhere the symbol is used."""
        await self._ensure_open(path)
        result = await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": path_to_uri(Path(path).resolve())},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
            timeout=timeout,
        )
        return as_location_list(result)

    async def hover(
        self, path: os.PathLike | str, line: int, character: int, *, timeout: float = 10.0
    ) -> Optional[HoverResult]:
        """``textDocument/hover`` — type / signature / docs at a position."""
        result = await self._position_request("textDocument/hover", path, line, character, timeout)
        if not result:
            return None
        return HoverResult.from_lsp(result)

    async def _position_request(
        self, method: str, path: os.PathLike | str, line: int, character: int, timeout: float
    ) -> Any:
        await self._ensure_open(path)
        return await self._request(
            method,
            {
                "textDocument": {"uri": path_to_uri(Path(path).resolve())},
                "position": {"line": line, "character": character},
            },
            timeout=timeout,
        )

    async def _ensure_open(self, path: os.PathLike | str) -> None:
        if canonical_key(Path(path).resolve()) not in self._versions:
            await self.open_document(path)

    # ── JSON-RPC plumbing ─────────────────────────────────────────────────────

    async def _request(self, method: str, params: Any, *, timeout: float = 20.0) -> Any:
        """Send a request and await its id-correlated response (timeout-bounded)."""
        if self._writer is None:
            raise LSPError("client not started")
        req_id = next(self._ids)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        await self._drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise LSPError(f"LSP request {method!r} timed out after {timeout}s") from exc

    def _notify(self, method: str, params: Any) -> None:
        if self._writer is None:
            raise LSPError("client not started")
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, obj: dict) -> None:
        assert self._writer is not None
        self._writer.write(encode_message(obj))

    async def _drain(self) -> None:
        if self._writer is not None:
            await self._writer.drain()

    async def _read_loop(self) -> None:
        """Read framed messages forever, routing each by shape (see module doc)."""
        assert self._reader is not None
        try:
            while True:
                msg = await read_message(self._reader)
                if msg is None:
                    break  # EOF — server closed the stream
                self._dispatch(msg)
        except asyncio.IncompleteReadError:
            logger.debug("LSP stream ended mid-message")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("LSP read loop error: %s", exc)
        finally:
            self._fail_pending(LSPError("LSP connection closed"))

    def _dispatch(self, msg: dict) -> None:
        # 1) Response to one of our requests (id + result/error).
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut is not None and not fut.done():
                if "error" in msg:
                    fut.set_exception(LSPError(f"LSP error: {msg['error']}"))
                else:
                    fut.set_result(msg.get("result"))
            return
        method = msg.get("method")
        # 2) Pushed diagnostics (notification, no id).
        if method == "textDocument/publishDiagnostics":
            self._on_publish_diagnostics(msg.get("params") or {})
            return
        # 3) Analysis-progress notifications — track busy/idle so get_diagnostics
        #    can wait for the server to finish (pyright/beginProgress..endProgress,
        #    and standard $/progress begin/end).
        if method in ("pyright/beginProgress", "window/workDoneProgress/begin") or (
            method == "$/progress" and _progress_kind(msg) == "begin"
        ):
            self._analysis_active += 1
            self._idle_event.clear()
            return
        if method in ("pyright/endProgress", "window/workDoneProgress/end") or (
            method == "$/progress" and _progress_kind(msg) == "end"
        ):
            self._analysis_active = max(0, self._analysis_active - 1)
            if self._analysis_active == 0:
                self._idle_event.set()
            return
        # 4) Server→client request (method + id) we don't implement: reply null so
        #    the server isn't left waiting (e.g. window/workDoneProgress/create).
        if "method" in msg and "id" in msg:
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            return
        # 5) Any other server notification (logMessage, reportProgress, …): ignore.

    def _on_publish_diagnostics(self, params: dict) -> None:
        uri = params.get("uri", "")
        if not uri:
            return
        key = uri_to_key(uri)
        diags = [Diagnostic.from_lsp(d) for d in params.get("diagnostics", [])]
        self._diagnostics[key] = diags
        event = self._diag_events.setdefault(key, asyncio.Event())
        event.set()  # wake any get_diagnostics waiter (it debounces on this)
        if self._on_diagnostics is not None:
            try:
                self._on_diagnostics(uri, diags)
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_diagnostics callback raised: %s", exc)

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
