"""Execution-time path containment for every file tool.

The deepagents builtins (``write_file``/``read_file``/``edit_file``/``ls``/``glob``/
``grep``) are bound to the ToolNode but are NOT containment-anchored, and the model
calls them from memory even when they're hidden from the model's menu — so a path
like ``/greeting.py`` escaped to the drive root (``D:\\greeting.py``). Menu
exclusion (``_ToolExclusionMiddleware``) is not enough because the builtins remain
bound and executable.

This middleware closes the gap at the only place that catches every call regardless
of menu state: the tool-execution seam. Its ``wrap_tool_call`` re-homes the path
argument of every file tool through :func:`hcode_v2.tools.files._resolve_path`
(which re-homes leading-slash/out-of-root paths under the working dir and rejects
``..`` traversal), then lets the call proceed with the contained, absolute path.
``_resolve_path`` returns an absolute in-root path; passing that to the builtins'
backend (``virtual_mode=False``) resolves correctly, so the builtin writes to the
right place. hcode's own tools are covered too (idempotent — they already contain).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from hcode_v2.tools.files import PathEscapeError, _resolve_path

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)

# Every tool that takes a filesystem path — both the deepagents builtins and
# hcode's own tools. A tool carries exactly one of the path keys below.
_FILE_TOOLS: frozenset[str] = frozenset(
    {
        # deepagents builtins
        "read_file", "write_file", "edit_file", "ls", "glob", "grep",
        # hcode tools
        "read", "write", "edit", "multi_edit",
    }
)
# Candidate path-arg keys across all file tools: builtins read/write/edit_file use
# "file_path"; ls/glob/grep (builtin + hcode) and read/write/edit/multi_edit use
# "path"; hcode glob uses "directory". A tool only ever carries one of these.
_PATH_KEYS: tuple[str, ...] = ("file_path", "path", "directory")


class _PathContainmentMiddleware(AgentMiddleware):
    """Re-home the path arg of every file tool through ``_resolve_path`` at
    execution time, so bound-but-uncontained builtins can't escape the working dir.
    """

    def _contain(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        """Return a request with the path arg re-homed, or a rejection ToolMessage.

        Non-file tools are returned unchanged. A path that escapes the working dir
        yields a ``ToolMessage`` (the caller must NOT execute it).
        """
        name = request.tool_call.get("name", "")
        if name not in _FILE_TOOLS:
            return request

        args = dict(request.tool_call.get("args", {}))
        for key in _PATH_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value:
                try:
                    args[key] = str(_resolve_path(value))  # contained absolute path
                except PathEscapeError:
                    return ToolMessage(
                        content=f"Error: path escapes the working directory: {value}",
                        tool_call_id=request.tool_call.get("id", ""),
                    )
        return request.override(tool_call={**request.tool_call, "args": args})

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Contain the file-tool path arg, then run the (possibly rewritten) call."""
        contained = self._contain(request)
        if isinstance(contained, ToolMessage):
            return contained  # escape rejected — do not execute
        return handler(contained)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async mirror of :meth:`wrap_tool_call`."""
        contained = self._contain(request)
        if isinstance(contained, ToolMessage):
            return contained
        return await handler(contained)


__all__ = ["_PathContainmentMiddleware"]
