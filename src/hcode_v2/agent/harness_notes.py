"""Harness clarity middleware — appends an authoritative correction note.

The vendored base stack's system prompts state things that don't match this
project's actual configuration:

- FilesystemMiddleware says "All file paths must start with a /" (a virtual-
  root convention) — but HCode's backend runs with ``virtual_mode=False`` and
  ``_PathContainmentMiddleware``/``files._resolve_path`` implement a DIFFERENT
  rule (relative-or-absolute-INSIDE-root; a leading slash is re-homed under
  root, not honored as a filesystem root). The model was being told BOTH
  conventions on every call — a standing contradiction (the class of bug
  behind the historical phantom-``/workspace/`` issue).
- FilesystemMiddleware/TodoListMiddleware also advertise ``read_file``,
  ``execute``, and ``write_todos`` by name — tools HCode excludes in favor of
  its own ``read``/``bash``/``todo_read``+``todo_write`` (see factory.py's
  excluded-tools set). Left alone, the model would be told about tools that no
  longer exist in its bound tool list.

Cannot edit the vendored prompts (Rule 1 — libs/deepagents is read-only).
Instead, this appends ONE short, authoritative correction on every model call,
using the SAME wrap_model_call + ``append_to_system_message`` mechanism PEV's
own phase prompts already use (pev.py, vendored, read-only reference).

Ordering guarantee: ``create_deep_agent`` always builds FilesystemMiddleware /
TodoListMiddleware FIRST, then extends the pipeline with HCode's own
``middleware=`` list (factory.py) — so this note's ``wrap_model_call`` runs
LATER in the chain and its text lands AFTER their prompt text in the final
system message on every call. The last word wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse


class HarnessNotesMiddleware(AgentMiddleware[Any, Any, Any]):
    """Appends a fixed authoritative note to every model call's system message.

    Args:
        note: Text to append. Kept generic (no hardcoded tool names) so the
            caller composes the note to match whatever it actually excluded.
    """

    def __init__(self, note: str) -> None:
        self._note = note

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Append the note, then delegate to the next handler/the model."""
        modified = request.override(
            system_message=append_to_system_message(request.system_message, self._note)
        )
        return handler(modified)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async variant of `wrap_model_call`."""
        modified = request.override(
            system_message=append_to_system_message(request.system_message, self._note)
        )
        return await handler(modified)


__all__ = ["HarnessNotesMiddleware"]
