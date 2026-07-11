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
from typing import Iterable, Optional

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
    "Language server not available - semantic checks skipped. "
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
        return f"No diagnostics - '{path}' is clean."
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
    label = noun if len(locs) == 1 else f"{noun}s"
    out = [f"{len(locs)} {label} for '{symbol}':"]
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


# ── PEV Verify integration (W3.3) ─────────────────────────────────────────────
#
# `verify_diagnostics_addendum` is the provider PEVMiddleware calls during the
# Verify phase. Given the files the agent edited this task, it runs the language
# server and — ONLY IF real errors exist — returns a prompt addendum listing them
# so the model emits `ISSUES FOUND` and self-corrects. It is OPTIONAL by
# construction: no server, no supported files, or no errors -> returns ``None``
# (PEV Verify then behaves exactly as it does today).
#
# ERRORS-ONLY RULE: diagnostics are filtered to ``Diagnostic.is_error`` before
# anything is returned. Warnings / info / hints can never produce an addendum and
# so can never trip the PEV error budget on benign findings.

_LSP_VERIFY_EVENT = "lsp_verify"


async def _emit_verify_event(payload: dict) -> None:
    """Dispatch a LangChain custom event so the UI can show LSP-verify progress.

    Surfaces as ``on_custom_event`` (name=``lsp_verify``) in ``astream_events``;
    the StreamingBridge maps it to a ``task_update``.  Best-effort: outside a
    runnable/callback context (e.g. unit tests calling the provider directly)
    this no-ops rather than raising.
    """
    try:
        from langchain_core.callbacks import adispatch_custom_event
        await adispatch_custom_event(_LSP_VERIFY_EVENT, payload)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break verify
        logger.debug("lsp_verify custom event not dispatched: %s", exc)


def _path_candidates(raw: str, root: Path) -> list[Path]:
    """Plausible on-disk locations for a path the model passed to a write/edit.

    Handles absolute paths, paths relative to the workspace root, and the
    virtual-root form (``/calculator.py`` -> ``{root}/calculator.py``) produced by
    the agent's ``virtual_mode`` backend.
    """
    p = Path(raw)
    cands: list[Path] = []
    if p.is_absolute():
        cands.append(p)
    cands.append(root / p)
    stripped = str(raw).lstrip("/\\")
    if stripped:
        cands.append(root / stripped)
    cands.append(p)
    return cands


def _resolve_verify_paths(paths: Iterable[str]) -> list[tuple[Path, "object"]]:
    """Resolve raw edited paths to ``(abspath, config)`` for existing, supported files.

    Unsupported file types and paths that don't resolve on disk are dropped
    (deduplicated) — the agent edits many things the language server can't check.
    """
    root = get_root_dir()
    out: list[tuple[Path, object]] = []
    seen: set[str] = set()
    for raw in paths:
        for cand in _path_candidates(raw, root):
            if cand.is_file():
                key = canonical_key(cand)
                if key in seen:
                    break
                cfg = config_for_path(str(cand))
                if cfg is not None:
                    seen.add(key)
                    out.append((cand, cfg))
                break
    return out


def _format_verify_addendum(errors_by_file: dict[str, list[Diagnostic]]) -> str:
    """Render the verify-prompt addendum from per-file error diagnostics."""
    total = sum(len(v) for v in errors_by_file.values())
    lines = [
        "## Language Server Diagnostics (Verify)",
        "",
        (
            f"A language server checked the file(s) you modified and found {total} "
            "ERROR(S). These are real type/syntax errors, not style warnings. You MUST "
            "output `ISSUES FOUND:` with a short summary so execution resumes and you can "
            "fix them. Do NOT output `VERIFIED OK` while these errors remain."
        ),
        "",
    ]
    for fpath, errs in errors_by_file.items():
        lines.append(f"### {_relativize(Path(fpath))}")
        for d in sorted(errs, key=lambda d: (d.line, d.range.start.character)):
            code = f" [{d.code}]" if d.code else ""
            lines.append(f"- line {d.line + 1}, col {d.range.start.character + 1}: {d.message}{code}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_post_edit_addendum(errors_by_file: dict[str, list[Diagnostic]]) -> str:
    """Render the post-edit tool-result addendum from per-file error diagnostics.

    Tool-result voice (vs the verify-prompt voice of ``_format_verify_addendum``):
    the model reads this as part of the edit's result and should fix immediately —
    no PEV verdict-marker instructions here, this fires in any phase.
    """
    total = sum(len(v) for v in errors_by_file.values())
    lines = [
        "",
        "--- Language server check (post-edit) ---",
        (
            f"pyright found {total} ERROR(S) in the file(s) after this edit. These are "
            "real type/syntax errors, not style warnings. Fix them now with another "
            "edit before continuing:"
        ),
    ]
    for fpath, errs in errors_by_file.items():
        lines.append(f"{_relativize(Path(fpath))}:")
        for d in sorted(errs, key=lambda d: (d.line, d.range.start.character)):
            code = f" [{d.code}]" if d.code else ""
            lines.append(f"- line {d.line + 1}, col {d.range.start.character + 1}: {d.message}{code}")
    return "\n".join(lines)


async def post_edit_diagnostics(paths: Iterable[str]) -> Optional[str]:
    """Post-edit gate provider: ERROR diagnostics for just-edited files.

    Same machinery as :func:`verify_diagnostics_addendum` (resolve paths -> sync
    documents -> errors-only filter -> ``lsp_verify`` progress events) but with a
    tool-result addendum instead of a verify-prompt addendum, so it can ride the
    edit tool's own result in ANY PEV phase. Returns ``None`` when there is no
    server, no supported file, or no errors — the caller then changes nothing
    (zero regression by construction). Never raises.

    Args:
        paths: File path(s) the tool call just wrote/edited (raw, as passed to
            the tool — absolute, root-relative, or virtual-root form).
    """
    try:
        candidates = _resolve_verify_paths(paths)
        usable = [(p, cfg) for p, cfg in candidates if lsp_available(cfg.language_id)]
        if not usable:
            return None  # no server / unsupported file -> behave as today

        await _emit_verify_event(
            {"status": "started", "fileCount": len(usable), "source": "post_edit"}
        )

        errors_by_file: dict[str, list[Diagnostic]] = {}
        for abspath, cfg in usable:
            client = await _MANAGER.get_client(cfg.language_id, get_root_dir())
            if client is None:
                continue
            try:
                await _sync_document(client, abspath)
                diags = await client.get_diagnostics(abspath)
            except (LSPError, OSError) as exc:
                logger.debug("post-edit diagnostics failed for %s: %s", abspath, exc)
                continue
            errors = [d for d in diags if d.is_error]  # ERRORS ONLY
            if errors:
                errors_by_file[str(abspath)] = errors

        total = sum(len(v) for v in errors_by_file.values())
        await _emit_verify_event(
            {"status": "done", "fileCount": len(usable), "errorCount": total,
             "source": "post_edit"}
        )
        if not errors_by_file:
            return None
        return _format_post_edit_addendum(errors_by_file)
    except Exception as exc:  # noqa: BLE001 — the gate must never break a tool call
        logger.debug("post_edit_diagnostics failed: %s", exc)
        return None


async def verify_diagnostics_addendum(paths: Iterable[str]) -> Optional[str]:
    """PEV-Verify provider: ERROR diagnostics for edited files as a prompt addendum.

    Returns a markdown block to append to the verify prompt **iff** the language
    server reports one or more ERROR-severity diagnostics in the supported files
    among *paths*; otherwise returns ``None``.  ``None`` means "no change" — PEV
    Verify proceeds exactly as it does without LSP.  Never raises.

    Args:
        paths: File paths the agent edited during this task (as passed to the
            write/edit tools — absolute, root-relative, or virtual-root form).
    """
    try:
        candidates = _resolve_verify_paths(paths)
        usable = [(p, cfg) for p, cfg in candidates if lsp_available(cfg.language_id)]
        if not usable:
            return None  # no server / no supported files -> behave as today

        await _emit_verify_event({"status": "started", "fileCount": len(usable)})

        errors_by_file: dict[str, list[Diagnostic]] = {}
        for abspath, cfg in usable:
            client = await _MANAGER.get_client(cfg.language_id, get_root_dir())
            if client is None:
                continue
            try:
                await _sync_document(client, abspath)
                diags = await client.get_diagnostics(abspath)
            except (LSPError, OSError) as exc:
                logger.debug("verify diagnostics failed for %s: %s", abspath, exc)
                continue
            errors = [d for d in diags if d.is_error]  # ERRORS ONLY
            if errors:
                errors_by_file[str(abspath)] = errors

        total = sum(len(v) for v in errors_by_file.values())
        await _emit_verify_event(
            {"status": "done", "fileCount": len(usable), "errorCount": total}
        )
        if not errors_by_file:
            return None
        return _format_verify_addendum(errors_by_file)
    except Exception as exc:  # noqa: BLE001 — verify must never break on LSP
        logger.debug("verify_diagnostics_addendum failed: %s", exc)
        return None
