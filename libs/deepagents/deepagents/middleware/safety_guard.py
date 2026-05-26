"""SafetyGuard middleware — file backup and restore before destructive tool calls."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langchain_core.messages import ToolMessage
    from langgraph.runtime import Runtime
    from langgraph.types import Command

logger = logging.getLogger(__name__)

_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "execute"})
_FILE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})
_FILE_ARG: str = "file_path"


def _default_file_reader(path: str) -> str | None:
    """Read file content, returning ``None`` if the file does not exist."""
    try:
        p = Path(path)
        return p.read_text() if p.exists() else None
    except OSError:
        return None


def _default_file_writer(path: str, content: str) -> None:
    """Write content to a file."""
    Path(path).write_text(content)


class SafetyState(AgentState):
    """Agent state extended with SafetyGuard transaction fields."""

    _safety_transaction_active: Annotated[NotRequired[bool], PrivateStateAttr]
    """``True`` while the agent task is running."""

    _safety_backups: Annotated[NotRequired[dict[str, str]], PrivateStateAttr]
    """Map of filename → original content captured before the first destructive write."""

    _safety_modified_files: Annotated[NotRequired[list[str]], PrivateStateAttr]
    """Files touched by write_file or edit_file calls during this task."""


class SafetyGuardMiddleware(AgentMiddleware):
    """Middleware that backs up file contents before destructive tool calls.

    For every ``write_file`` or ``edit_file`` call, the current file content is
    read and stored **before** the tool executes.  If the tool raises an
    exception the backup is immediately restored.  A list of all modified file
    paths is accumulated and exposed for inspection.

    Backups are kept in memory on the middleware instance (not LangGraph state)
    so that rollbacks happen synchronously inside ``wrap_tool_call``.  The state
    fields ``_safety_backups`` and ``_safety_modified_files`` are populated as a
    final snapshot when ``after_agent`` runs.

    Args:
        file_reader: Callable ``(path) -> content | None``.  Returns ``None``
            when the file does not exist.  Defaults to ``pathlib.Path.read_text``.
        file_writer: Callable ``(path, content) -> None``.  Defaults to
            ``pathlib.Path.write_text``.
    """

    state_schema = SafetyState

    def __init__(
        self,
        *,
        file_reader: Callable[[str], str | None] | None = None,
        file_writer: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initialise SafetyGuardMiddleware with optional I/O callbacks.

        Args:
            file_reader: Override for reading file content before writes.
            file_writer: Override for restoring file content after failures.
        """
        self._file_reader: Callable[[str], str | None] = file_reader or _default_file_reader
        self._file_writer: Callable[[str, str], None] = file_writer or _default_file_writer
        self._in_flight_backups: dict[str, str] = {}
        self._in_flight_modified: list[str] = []

    def before_agent(self, state: SafetyState, runtime: Runtime) -> dict[str, Any] | None:
        """Reset transaction state and mark the session as active.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update enabling the safety transaction.
        """
        self._in_flight_backups = {}
        self._in_flight_modified = []
        return {
            "_safety_transaction_active": True,
            "_safety_backups": {},
            "_safety_modified_files": [],
        }

    async def abefore_agent(self, state: SafetyState, runtime: Runtime) -> dict[str, Any] | None:
        """Delegate asynchronously to :meth:`before_agent`.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update enabling the safety transaction.
        """
        return self.before_agent(state, runtime)

    def _backup_and_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Back up a file (if applicable), call handler, and restore on failure.

        Args:
            request: Tool call request.
            handler: Tool execution handler.

        Returns:
            Tool result from the handler.
        """
        tool_name: str = request.tool_call["name"]

        if tool_name not in _DESTRUCTIVE_TOOLS:
            return handler(request)

        args: dict[str, Any] = request.tool_call.get("args", {})
        filepath: str | None = args.get(_FILE_ARG) if tool_name in _FILE_TOOLS else None

        if filepath:
            existing = self._file_reader(filepath)
            if existing is not None:
                self._in_flight_backups[filepath] = existing

        try:
            result = handler(request)
        except Exception:
            if filepath and filepath in self._in_flight_backups:
                self._file_writer(filepath, self._in_flight_backups[filepath])
            raise

        if filepath:
            self._in_flight_modified.append(filepath)

        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Intercept synchronous tool calls to back up files before destructive writes.

        Args:
            request: Tool call request from the agent loop.
            handler: Next handler in the middleware chain.

        Returns:
            Tool result, with automatic backup restore on exception.
        """
        return self._backup_and_call(request, handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Intercept asynchronous tool calls to back up files before destructive writes.

        Args:
            request: Tool call request from the agent loop.
            handler: Next async handler in the middleware chain.

        Returns:
            Tool result, with automatic backup restore on exception.
        """
        tool_name: str = request.tool_call["name"]

        if tool_name not in _DESTRUCTIVE_TOOLS:
            return await handler(request)

        args: dict[str, Any] = request.tool_call.get("args", {})
        filepath: str | None = args.get(_FILE_ARG) if tool_name in _FILE_TOOLS else None

        if filepath:
            existing = self._file_reader(filepath)
            if existing is not None:
                self._in_flight_backups[filepath] = existing

        try:
            result = await handler(request)
        except Exception:
            if filepath and filepath in self._in_flight_backups:
                self._file_writer(filepath, self._in_flight_backups[filepath])
            raise

        if filepath:
            self._in_flight_modified.append(filepath)

        return result

    def after_agent(self, state: SafetyState, runtime: Runtime) -> dict[str, Any] | None:
        """Close the transaction and log a summary of modified files.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update marking the transaction as inactive and snapshotting
            the modified file list and backups.
        """
        count = len(self._in_flight_modified)
        logger.info("SafetyGuard: %d file(s) modified during task: %s", count, self._in_flight_modified)
        return {
            "_safety_transaction_active": False,
            "_safety_modified_files": list(self._in_flight_modified),
            "_safety_backups": dict(self._in_flight_backups),
        }

    async def aafter_agent(self, state: SafetyState, runtime: Runtime) -> dict[str, Any] | None:
        """Delegate asynchronously to :meth:`after_agent`.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update marking the transaction as inactive.
        """
        return self.after_agent(state, runtime)

    def get_modified_files(self) -> list[str]:
        """Return the list of file paths modified since the last :meth:`before_agent` call.

        Returns:
            Copy of the in-flight modified file list.
        """
        return list(self._in_flight_modified)

    def get_backup(self, filename: str) -> str | None:
        """Return the backed-up content for ``filename``, or ``None`` if not backed up.

        Args:
            filename: Absolute file path that was backed up.

        Returns:
            Original file content, or ``None`` if no backup exists.
        """
        return self._in_flight_backups.get(filename)


__all__ = ["SafetyGuardMiddleware", "SafetyState"]
