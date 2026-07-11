"""Post-edit LSP gate — a type-check safety net for EVERY file edit, any phase.

The Verify-phase LSP integration (W3.3) only fires when a task reaches the
``verify`` phase — and PEV's TaskClassifier routes ordinary tasks to ``fast``,
which never transitions. So the edits most users actually make got no language
server check at all. Forcing the full arc (the composer's Plan mode) fixes it at
the cost of plan+verify model round-trips — too expensive for a one-line edit.

``PostEditLspMiddleware`` closes the gap at the tool-execution seam instead
(the same seam ``_PathContainmentMiddleware`` guards): ``awrap_tool_call`` lets
the write/edit tool run, then — for successful edits to LSP-supported files —
runs the language server on JUST the edited file(s):

* **CLEAN** → the tool result is returned untouched. No model round-trip, no
  user noise; pyright is local and served by the persistent per-workspace client
  (``lsp_tools._LSPClientManager``), so the check is fast.
* **ERRORS** (errors only — warnings never trigger, matching the verify-lane
  policy) → an actionable diagnostics addendum is appended to the tool RESULT.
  The model reads that result on its next turn anyway, so self-correction costs
  zero additional round-trips beyond the fix the model was going to need.

Loop safety: at most ``_MAX_FIX_ATTEMPTS`` addenda per file per task. The
counter resets when the file checks clean and on every new task
(``before_agent``). Once capped, the gate goes silent for that file — PEV's
verify lane (when the arc runs) and the model's own judgment take over.

Phase interaction: in the ``verify`` phase this gate is structurally
unreachable — PEV strips mutating tools there (pev.py ``_build_modified_request``)
— and it additionally skips on ``_pev_phase == "verify"`` defensively, so the
verify-lane provider can never be doubled. In ``plan`` phase no tools are bound
at all. In ``execute``/``fast`` it fires — ``fast`` is the whole point.

Kill-switch: the factory only attaches this middleware when
``HCODE_POST_EDIT_LSP`` is not ``0``/``false``/``off`` (default ON). Absent
middleware = byte-identical behaviour. With no language server installed, or for
non-code files, the provider returns ``None`` and the result passes through
untouched — zero regression by construction. HCode-side only; vendored
deepagents untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from hcode_v2.tools.lsp_tools import post_edit_diagnostics

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import AgentState, ToolCallRequest
    from langgraph.runtime import Runtime
    from langgraph.types import Command

logger = logging.getLogger(__name__)

# Mutating file tools whose results carry code the server should check. Includes
# the deepagents builtin names: they are menu-excluded but remain bound, and the
# model can still call them from memory (same rationale as path containment).
_MUTATING_FILE_TOOLS: frozenset[str] = frozenset(
    {"write", "edit", "multi_edit", "write_file", "edit_file"}
)
# hcode tools use "path"; the deepagents builtins use "file_path".
_PATH_KEYS: tuple[str, ...] = ("path", "file_path")

_MAX_FIX_ATTEMPTS: int = 2
"""Addenda appended per file per task before the gate goes silent for that file."""


class PostEditLspMiddleware(AgentMiddleware):
    """Run the language server on every successful file edit; feed errors back
    through the tool result so the model fixes them in its natural next turn.
    """

    def __init__(self) -> None:
        super().__init__()
        # path -> number of error-addenda appended this task (reset per task and
        # on a clean check). Instance state is safe: one agent per thread, one
        # task at a time (daemon single-flight), and the async path never runs
        # two tool wrappers of the same agent concurrently for the same file.
        self._attempts: dict[str, int] = {}

    # ── Per-task reset ────────────────────────────────────────────────────────

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Reset the per-file fix-attempt counters at the start of every task."""
        self._attempts.clear()
        return None

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    # ── The gate ──────────────────────────────────────────────────────────────

    @staticmethod
    def _edited_path(request: ToolCallRequest) -> str | None:
        """The path argument of a mutating file-tool call, or ``None``."""
        name = request.tool_call.get("name", "")
        if name not in _MUTATING_FILE_TOOLS:
            return None
        args = request.tool_call.get("args", {}) or {}
        for key in _PATH_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Run the tool, then check the edited file and append errors (if any).

        Async-only, mirroring PEV's diagnostics injection: both agent entrypoints
        (daemon and CLI) drive the graph async. The sync ``wrap_tool_call`` stays
        the base-class passthrough.
        """
        result = await handler(request)

        path = self._edited_path(request)
        if path is None:
            return result  # not a mutating file tool
        if not isinstance(result, ToolMessage):
            return result  # Command result — nothing to append to
        content = result.content
        if not isinstance(content, str) or content.lstrip().startswith("Error"):
            return result  # failed edit (file unchanged) or non-text content
        # Defensive: never double-check in the verify phase. Structurally
        # unreachable (verify strips mutating tools), but state is authoritative.
        try:
            if (request.state or {}).get("_pev_phase") == "verify":
                return result
        except Exception:  # noqa: BLE001 — state access must never break the call
            pass

        attempts = self._attempts.get(path, 0)
        if attempts >= _MAX_FIX_ATTEMPTS:
            return result  # capped: go silent for this file this task

        try:
            addendum = await post_edit_diagnostics([path])
        except Exception as exc:  # noqa: BLE001 — the gate must never break a tool call
            logger.debug("post-edit LSP gate skipped (%s): %s", path, exc)
            return result

        if addendum is None:
            # Clean (or no server / unsupported file): pass through untouched and
            # re-arm the counter so a later regression on this file alerts again.
            self._attempts.pop(path, None)
            return result

        self._attempts[path] = attempts + 1
        capped_note = (
            "\n(Note: this is the final automatic check for this file in this task.)"
            if self._attempts[path] >= _MAX_FIX_ATTEMPTS
            else ""
        )
        return ToolMessage(
            content=content + addendum + capped_note,
            tool_call_id=result.tool_call_id,
            name=result.name,
            artifact=getattr(result, "artifact", None),
            status=result.status,
        )


__all__ = ["PostEditLspMiddleware"]
