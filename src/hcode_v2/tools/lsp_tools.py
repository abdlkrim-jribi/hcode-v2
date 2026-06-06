"""LSP-backed semantic code tools (W3.2).

Wraps the W3.1 `LSPClient` as LangChain tools so the model can ask a language
server real questions about code *mid-task*:

  * ``check_diagnostics(path)``      — type/syntax errors & warnings (the big one)
  * ``goto_definition(path, symbol)`` — where a symbol is defined
  * ``find_references(path, symbol)`` — where a symbol is used
  * ``hover_info(path, symbol)``      — type / signature / docs at a symbol

These tools are **async** because both agent entrypoints (daemon and CLI) run
the graph inside an event loop (``ainvoke`` / ``astream_events``); LangGraph
awaits coroutine tools directly.

Lifecycle
---------
One pyright process is **reused per workspace** via a module-level, loop-aware
`_LSPClientManager` — we never spawn a server per call.  The manager keys clients
by ``(language_id, workspace_root)`` and transparently rebuilds if the running
event loop changed (e.g. a fresh ``asyncio.run``) or the server died.

Optional by construction
-------------------------
Every tool degrades to a clear, human-readable message — never an exception —
when no language server is installed (`lsp_available` is False), when no server
is configured for the file's type, or when a query errors/times out.  With
pyright absent the tools simply report that semantic checks were skipped.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from hcode_v2.lsp import (
    Diagnostic,
    DiagnosticSeverity,
    LSPClient,
    LSPError,
    Location,
    canonical_key,
    config_for_path,
    get_config,
    lsp_available,
)
from hcode_v2.tools.base import get_root_dir

logger = logging.getLogger(__name__)

_UNAVAILABLE_MSG = (
    "Language server not available — semantic checks skipped. "
    "This is optional; install a server to enable them "
    "(e.g. `npm install -g pyright` for Python)."
)


# ── Long-lived client manager (one server per workspace) ─────────────────────

class _LSPClientManager:
    """Caches one started `LSPClient` per ``(language_id, workspace_root)``.

    Loop-aware: an asyncio subprocess is bound to the loop that created it, so we
    remember that loop and rebuild the client if a later call runs on a different
    loop (separate ``asyncio.run``) or the server has exited.
    """

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], tuple[LSPClient, asyncio.AbstractEventLoop]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(id(loop))
        if lock is None:
            lock = asyncio.Lock()
            self._locks[id(loop)] = lock
        return lock

    async def get_client(self, language_id: str, root: Path) -> Optional[LSPClient]:
        """Return a started client, or ``None`` if the server is unavailable."""
        if not lsp_available(language_id):
            return None
        loop = asyncio.get_running_loop()
        key = (language_id, canonical_key(root))

        cached = self._clients.get(key)
        if cached is not None:
            client, client_loop = cached
            if client_loop is loop and client.is_running:
                return client
            # Stale: loop changed or server died. Drop it (don't await across a
            # dead/foreign loop) and rebuild below.
            self._clients.pop(key, None)

        async with self._lock():
            cached = self._clients.get(key)
            if cached is not None and cached[1] is loop and cached[0].is_running:
                return cached[0]
            cfg = get_config(language_id)
            if cfg is None:
                return None
            client = LSPClient(cfg, root)
            try:
                await client.start()
            except LSPError as exc:  # includes LSPUnavailable
                logger.debug("LSP server failed to start: %s", exc)
                return None
            self._clients[key] = (client, loop)
            return client

    async def shutdown_all(self) -> None:
        """Stop every cached client (best-effort). For graceful process exit."""
        clients = list(self._clients.values())
        self._clients.clear()
        for client, _loop in clients:
            try:
                await client.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("error stopping LSP client: %s", exc)


_MANAGER = _LSPClientManager()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else get_root_dir() / p


def _relativize(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(get_root_dir().resolve()))
    except (ValueError, OSError):
        return str(p)


async def _acquire(abspath: Path) -> tuple[Optional[LSPClient], Optional[str]]:
    """Resolve a started client for *abspath*, or a degradation message.

    Returns ``(client, None)`` on success or ``(None, message)`` when no server
    applies — the message is what the tool should return to the model.
    """
    cfg = config_for_path(str(abspath))
    if cfg is None:
        return None, (
            f"No language server is configured for '{abspath.suffix or abspath.name}' files; "
            "semantic checks skipped."
        )
    if not lsp_available(cfg.language_id):
        return None, _UNAVAILABLE_MSG
    client = await _MANAGER.get_client(cfg.language_id, get_root_dir())
    if client is None:
        return None, _UNAVAILABLE_MSG
    return client, None


async def _sync_document(client: LSPClient, abspath: Path) -> None:
    """Push the file's current on-disk content to the server before querying.

    The agent edits files with the `write`/`edit` tools, which the language
    server knows nothing about — so we re-sync from disk so diagnostics and
    positions reflect what the model is actually looking at.
    """
    text = abspath.read_text(encoding="utf-8")
    await client.update_document(abspath, text)


def _find_symbol_position(text: str, symbol: str, line: Optional[int]) -> Optional[tuple[int, int]]:
    """First word-boundary occurrence of *symbol* → (0-based line, character).

    If *line* (1-based) is given, only that line is searched.  ``character`` is a
    code-point offset, which equals the UTF-16 unit offset for ASCII identifiers
    (the common case for code symbols).
    """
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    lines = text.splitlines()
    if line is not None:
        idx = line - 1
        if 0 <= idx < len(lines):
            m = pattern.search(lines[idx])
            if m:
                return idx, m.start()
        return None
    for i, ln in enumerate(lines):
        m = pattern.search(ln)
        if m:
            return i, m.start()
    return None


def _format_diagnostics(path: str, diags: list[Diagnostic]) -> str:
    if not diags:
        return f"No diagnostics — '{path}' is clean."
    errors = [d for d in diags if d.is_error]
    warnings = [d for d in diags if d.severity == DiagnosticSeverity.WARNING]
    others = [d for d in diags if d not in errors and d not in warnings]

    header = f"{len(errors)} error(s), {len(warnings)} warning(s)"
    if others:
        header += f", {len(others)} info/hint"
    header += f" in {path}:"

    out = [header]
    for d in sorted(diags, key=lambda d: (d.line, d.range.start.character)):
        sev = d.severity.name if d.severity else "INFO"
        code = f" [{d.code}]" if d.code else ""
        src = f" ({d.source})" if d.source else ""
        # LSP positions are 0-based; report 1-based to match the `read` tool.
        out.append(
            f"  {path}:{d.line + 1}:{d.range.start.character + 1}: "
            f"{sev}: {d.message}{code}{src}"
        )
    return "\n".join(out)


def _format_locations(symbol: str, locs: list[Location], noun: str) -> str:
    if not locs:
        return f"No {noun} found for '{symbol}'."
    out = [f"{len(locs)} {noun} for '{symbol}':"]
    for loc in locs:
        rel = _relativize(loc.path)
        out.append(f"  {rel}:{loc.range.start.line + 1}:{loc.range.start.character + 1}")
    return "\n".join(out)


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
async def check_diagnostics(path: str) -> str:
    """Check a file for type errors, syntax errors and warnings via a language server.

    Use this after writing or editing code to verify it is semantically correct
    before moving on. Returns a list of diagnostics with 1-based line:column,
    severity, message and rule code — or a note that the file is clean. If no
    language server is installed this is skipped gracefully.

    Args:
        path: Relative or absolute path to the source file to check.
    """
    abspath = _resolve_path(path)
    if not abspath.exists():
        return f"Error: file not found: {path}"
    client, msg = await _acquire(abspath)
    if client is None:
        return msg  # type: ignore[return-value]
    try:
        await _sync_document(client, abspath)
        diags = await client.get_diagnostics(abspath)
    except LSPError as exc:
        return f"Language server error while checking '{path}': {exc}"
    except OSError as exc:
        return f"Error reading '{path}': {exc}"
    return _format_diagnostics(path, diags)


@tool
async def goto_definition(path: str, symbol: str, line: Optional[int] = None) -> str:
    """Find where a symbol is defined, using a language server.

    Resolves *symbol* to its first occurrence in *path* (or on *line* if given)
    and asks the language server for its definition site(s).

    Args:
        path: Source file containing a use of the symbol.
        symbol: The identifier to look up (e.g. a function or class name).
        line: Optional 1-based line to disambiguate when the symbol repeats.
    """
    abspath = _resolve_path(path)
    if not abspath.exists():
        return f"Error: file not found: {path}"
    client, msg = await _acquire(abspath)
    if client is None:
        return msg  # type: ignore[return-value]
    try:
        await _sync_document(client, abspath)
        pos = _find_symbol_position(abspath.read_text(encoding="utf-8"), symbol, line)
        if pos is None:
            return f"Error: symbol '{symbol}' not found in {path}."
        locs = await client.goto_definition(abspath, pos[0], pos[1])
    except LSPError as exc:
        return f"Language server error: {exc}"
    except OSError as exc:
        return f"Error reading '{path}': {exc}"
    return _format_locations(symbol, locs, "definition")


@tool
async def find_references(path: str, symbol: str, line: Optional[int] = None) -> str:
    """Find everywhere a symbol is used, using a language server.

    Resolves *symbol* to its first occurrence in *path* (or on *line* if given)
    and asks the language server for all references — useful for impact analysis
    before renaming or changing a function.

    Args:
        path: Source file containing the symbol.
        symbol: The identifier to find references to.
        line: Optional 1-based line to disambiguate when the symbol repeats.
    """
    abspath = _resolve_path(path)
    if not abspath.exists():
        return f"Error: file not found: {path}"
    client, msg = await _acquire(abspath)
    if client is None:
        return msg  # type: ignore[return-value]
    try:
        await _sync_document(client, abspath)
        pos = _find_symbol_position(abspath.read_text(encoding="utf-8"), symbol, line)
        if pos is None:
            return f"Error: symbol '{symbol}' not found in {path}."
        locs = await client.find_references(abspath, pos[0], pos[1])
    except LSPError as exc:
        return f"Language server error: {exc}"
    except OSError as exc:
        return f"Error reading '{path}': {exc}"
    return _format_locations(symbol, locs, "reference")


@tool
async def hover_info(path: str, symbol: str, line: Optional[int] = None) -> str:
    """Get type / signature / docs for a symbol, using a language server.

    Resolves *symbol* to its first occurrence in *path* (or on *line* if given)
    and returns the language server's hover text (inferred type, signature,
    docstring).

    Args:
        path: Source file containing the symbol.
        symbol: The identifier to inspect.
        line: Optional 1-based line to disambiguate when the symbol repeats.
    """
    abspath = _resolve_path(path)
    if not abspath.exists():
        return f"Error: file not found: {path}"
    client, msg = await _acquire(abspath)
    if client is None:
        return msg  # type: ignore[return-value]
    try:
        await _sync_document(client, abspath)
        pos = _find_symbol_position(abspath.read_text(encoding="utf-8"), symbol, line)
        if pos is None:
            return f"Error: symbol '{symbol}' not found in {path}."
        hover = await client.hover(abspath, pos[0], pos[1])
    except LSPError as exc:
        return f"Language server error: {exc}"
    except OSError as exc:
        return f"Error reading '{path}': {exc}"
    if hover is None or not hover.value.strip():
        return f"No hover information for '{symbol}' in {path}."
    return f"Hover for '{symbol}':\n{hover.value}"
