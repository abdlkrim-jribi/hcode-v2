"""Rich live display for a chat turn — consumes the raw LangGraph event stream.

Reads ``agent.astream_events(..., version="v2")`` events directly. The event
names and payload shapes mirror what the daemon's StreamingBridge consumes,
but this module is CLI-local: it imports nothing from ``hcode_v2.daemon`` and
is display-only — no approval gate, no interrupt.

Raw events consumed
-------------------
on_chat_model_stream  Accumulate tokens. When the PEV "PLAN COMPLETE" marker
                      appears, print the accumulated plan once in a "Plan"
                      panel; "EXECUTION COMPLETE" just flips internal phase.
on_tool_start         ``todo_write`` / ``write_todos``: replace the live
                      progress checklist from the tool input. Other tools:
                      show a lightweight "running <tool>" line.
on_tool_end           Clear the running-tool line.
on_chat_model_end     Collect answer candidates. The user-facing answer is
                      the last real (execute-phase / plain chat) text; a
                      verify verdict (VERIFIED OK / ISSUES FOUND) becomes a
                      short status note, and a plan echo is never the answer
                      unless nothing else exists.

Trivial/fast turns (no plan marker, no todos) render nothing extra, and a
malformed or unknown event never raises — rendering must not break the turn.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

logger = logging.getLogger(__name__)

# Both todo tools surface their full list in the on_tool_start input:
#   hcode todo_write       — todos=[{text, done}]
#   deepagents write_todos — todos=[{content, status: pending|in_progress|completed}]
_TODO_TOOLS: frozenset[str] = frozenset({"todo_write", "write_todos"})

# PEV phase markers, matching PEVMiddleware's own text-based detection.
_PLAN_MARKER = "PLAN COMPLETE"
_EXEC_MARKER = "EXECUTION COMPLETE"
_VERDICT_OK = "VERIFIED OK"
_VERDICT_ISSUES = "ISSUES FOUND"
_ALL_MARKERS = (_PLAN_MARKER, _EXEC_MARKER, _VERDICT_OK, _VERDICT_ISSUES)


def _strip_markers(text: str) -> str:
    """Remove PEV protocol markers from user-facing text, tidying blank lines."""
    for marker in _ALL_MARKERS:
        text = re.sub(re.escape(marker), "", text, flags=re.IGNORECASE)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()

# checklist marker + style per normalized status (ASCII-safe for Windows consoles)
_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "done": ("x", "green"),
    "running": (">", "yellow"),
    "pending": (" ", "dim"),
}


def _text_from_content(content: Any) -> str:
    """Normalize an AI message/chunk ``content`` to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return ""


def _todo_items(raw: Any) -> list[tuple[str, str]]:
    """Normalize a todo tool input list to ``(status, text)`` pairs.

    Accepts both todo shapes (dicts or attribute objects); items without a
    usable text are skipped rather than rendered or raised on.
    """
    items: list[tuple[str, str]] = []
    if not isinstance(raw, (list, tuple)):
        return items
    for item in raw:
        if isinstance(item, dict):
            get: Callable[[str], Any] = item.get
        else:
            def get(key: str, _item: Any = item) -> Any:
                return getattr(_item, key, None)
        text = get("text") or get("content")
        if not text:
            continue
        status_value = get("status")
        if status_value is not None:
            status = {"completed": "done", "in_progress": "running"}.get(
                str(status_value), "pending"
            )
        else:
            status = "done" if get("done") else "pending"
        items.append((status, str(text)))
    return items


class LiveTurnRenderer:
    """Live plan + progress display for one chat turn.

    Use as a context manager around the ``astream_events`` loop and feed every
    raw event to :meth:`process_event`. After the stream ends, the user-facing
    answer is available as :attr:`final_text` and the verify outcome (if any)
    as :attr:`verify_status`.

    One instance per turn; do not reuse.

    Args:
        console: Rich console to render to (the chat display's console).
    """

    def __init__(self, console: Console, show_todos: bool = True) -> None:
        self.console = console
        self.show_todos = show_todos
        self.verify_status: str | None = None
        self._answer_text: str = ""
        self._verdict_text: str = ""
        self._plan_echo_text: str = ""
        self._token_buffer: str = ""
        self._plan_shown: bool = False
        self._exec_done: bool = False
        self._todos: list[tuple[str, str]] = []
        self._active_tool: str | None = None
        self._live: Live | None = None

    @property
    def final_text(self) -> str:
        """User-facing answer: real text first, then stripped verdict, then plan."""
        return self._answer_text or self._verdict_text or self._plan_echo_text

    # ── Context manager (Live lifecycle) ────────────────────────────────────

    def __enter__(self) -> "LiveTurnRenderer":
        self._live = Live(
            self.renderable(),
            console=self.console,
            refresh_per_second=8,
        )
        self._live.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    # ── Event consumption ────────────────────────────────────────────────────

    def process_event(self, event: dict) -> None:
        """Dispatch one raw astream_events event. Never raises."""
        try:
            kind: str = event.get("event", "")
            name: str = event.get("name", "")
            data: dict = event.get("data") or {}

            if kind == "on_chat_model_stream":
                self._on_token(data)
            elif kind == "on_tool_start":
                self._on_tool_start(name, data)
            elif kind == "on_tool_end":
                self._on_tool_end()
            elif kind == "on_chat_model_end":
                self._on_model_end(data)
        except Exception:  # noqa: BLE001 — display-only, never break the turn
            logger.debug("live renderer ignored a malformed event", exc_info=True)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def renderable(self) -> RenderableType:
        """Return the current live region content (checklist + running tool)."""
        lines: list[RenderableType] = []
        if self.show_todos:
            for status, text in self._todos:
                marker, style = _STATUS_STYLES.get(status, _STATUS_STYLES["pending"])
                lines.append(Text(f"[{marker}] {text}", style=style))
        if self._active_tool:
            lines.append(Text(f"running {self._active_tool}...", style="dim"))
        return Group(*lines) if lines else Text("")

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.renderable())

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_token(self, data: dict) -> None:
        chunk = data.get("chunk")
        if chunk is None:
            return
        text = _text_from_content(getattr(chunk, "content", ""))
        if not text:
            return
        self._token_buffer += text
        upper = self._token_buffer.upper()

        if not self._plan_shown and _PLAN_MARKER in upper:
            self._plan_shown = True
            plan_text = self._token_buffer.strip()
            # Printed (not Live-rendered) so the plan stays in the scrollback
            # above the live progress region.
            self.console.print(
                Panel(Markdown(plan_text), title="Plan", border_style="cyan")
            )
            self._token_buffer = ""
        elif self._plan_shown and not self._exec_done and _EXEC_MARKER in upper:
            self._exec_done = True
            self._token_buffer = ""

    def _on_tool_start(self, name: str, data: dict) -> None:
        if name in _TODO_TOOLS:
            tool_input = data.get("input")
            todos = tool_input.get("todos") if isinstance(tool_input, dict) else None
            items = _todo_items(todos)
            if items:
                self._todos = items
        else:
            self._active_tool = name
        self._refresh()

    def _on_tool_end(self) -> None:
        self._active_tool = None
        self._refresh()

    def _on_model_end(self, data: dict) -> None:
        """Classify each model output by its content markers.

        The verify verdict is the LAST output of a PEV turn and often echoes
        the plan — it must become a status note, not the displayed answer.
        Classification is by content (not by phase flags) because the marker
        streams in tokens before the same call's ``on_chat_model_end``.
        """
        output = data.get("output")
        text = _text_from_content(getattr(output, "content", "")).strip()
        if not text:
            return
        upper = text.upper()

        if _VERDICT_OK in upper or _VERDICT_ISSUES in upper:
            status = "verified" if _VERDICT_OK in upper else "issues found"
            if self.verify_status != status:
                self.verify_status = status
                style = "green" if status == "verified" else "yellow"
                self.console.print(Text(status, style=f"dim {style}"))
            self._verdict_text = _strip_markers(text)
        elif _PLAN_MARKER in upper:
            # Plan echo — already rendered in the Plan panel above.
            self._plan_echo_text = _strip_markers(text)
        else:
            # A bare "EXECUTION COMPLETE" strips to empty — it must not wipe
            # an earlier real answer.
            stripped = _strip_markers(text)
            if stripped:
                self._answer_text = stripped


__all__ = ["LiveTurnRenderer"]
