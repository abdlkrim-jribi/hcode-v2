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
on_tool_end           Clear the running-tool line and append a persistent
                      completed-action feed entry — "✓ <verb> <path>" plus a
                      coloured inline diff read from the tool's ``.artifact``
                      (hcode edit/write/multi_edit); read-side tools add just
                      the action line.
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
import os
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


def _is_marker_fragment(line: str) -> bool:
    """True if ``line`` (alone) is a truncated PEV marker, e.g. "EXEC".

    A fragment is a non-trivial (len>=3) case-insensitive PREFIX of some known
    marker — what's left when the model hits ``max_tokens`` mid-marker. The
    whole-line check keeps it conservative: real prose like "execute the plan"
    is multiple words and won't equal a marker prefix, so it's never nuked.
    """
    token = line.strip().upper()
    if len(token) < 3:
        return False
    return any(marker.upper().startswith(token) for marker in _ALL_MARKERS)


def _strip_markers(text: str) -> str:
    """Remove PEV protocol markers from user-facing text, tidying blank lines."""
    for marker in _ALL_MARKERS:
        text = re.sub(re.escape(marker), "", text, flags=re.IGNORECASE)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    # Drop a truncated marker fragment left alone on the final line (the model
    # was cut off mid-"EXECUTION COMPLETE" -> "EXEC"). Only the last line, only
    # when the whole line is a marker prefix — never mid-prose.
    if lines and _is_marker_fragment(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()

# checklist marker + style per normalized status (ASCII-safe for Windows consoles)
_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "done": ("x", "green"),
    "running": (">", "yellow"),
    "pending": (" ", "dim"),
}

# Friendly past-tense verb per tool name — covers BOTH the deepagents builtins
# (read_file/write_file/edit_file/ls, input key "file_path") and hcode's own
# tools (read/write/edit/multi_edit, input key "path"). Unknown -> the raw name.
_TOOL_LABELS: dict[str, str] = {
    "read_file": "Read",
    "read": "Read",
    "write_file": "Wrote",
    "write": "Wrote",
    "edit_file": "Edited",
    "edit": "Edited",
    "multi_edit": "Edited",
    "ls": "Listed",
    "execute": "Bash",
    "bash": "Bash",
}
# Completed-action glyph (the app runs with PYTHONIOENCODING=utf-8).
_DONE_GLYPH = "✓"
# Cap inline diff lines so a huge edit can't flood the feed.
_MAX_DIFF_LINES = 40
# Verbs that count as a file mutation for the end-of-turn outcome summary
# (read/ls actions are shown only when there are no mutations).
_MUTATION_VERBS: frozenset[str] = frozenset({"Wrote", "Created", "Edited"})

# ── KPIT brand theme (single source of truth) ────────────────────────────────
# Sampled from the KPIT logo: lime green primary + a dimmed variant; a brand
# purple for edits (used in slice 2). Rich style strings reference these hexes.
KPIT_GREEN = "#AEFC42"      # WRITE accent — header verb + ✓
KPIT_GREEN_DIM = "#7DB52F"  # gutter / secondary / BASH
KPIT_PURPLE = "#9B5DE5"     # EDIT accent (slice 2)

# Per-action header icons (the verb already carries colour; the icon adds glance
# value). READ/LS stay iconless — they keep the compact "✓ Read <path>" line.
ICON_WRITE = "\U0001F4C4"   # 📄 write/write_file
ICON_EDIT = "✏️"  # ✏️ edit/edit_file/multi_edit (slice 2 — not wired yet)
ICON_BASH = "▶"        # ▶ execute/bash (monochrome — aligns better than emoji)

# Show at most this many head + tail lines of a long write/bash body before
# collapsing the middle (mirrors v1's 4-head/4-tail truncation).
_BODY_HEAD = 4
_BODY_TAIL = 4
_BODY_FULL_MAX = 10  # bodies this short are shown in full
# Truncate a bash command in the header to keep the line tidy.
_CMD_MAX = 70


def _relative_path(path: str) -> str:
    """Display a path relative to the working dir; fall back to the raw path."""
    try:
        base = os.environ.get("HCODE_ROOT_DIR") or os.getcwd()
        return os.path.relpath(path, base)
    except Exception:  # noqa: BLE001 — display-only, odd/cross-drive paths must not crash
        return path


def _diff_line_style(line: str) -> str:
    """Rich style for one diff content line (+ green / - red / context dim).

    Header lines (``+++``/``---``/``@@``) are dropped before this is called, so
    no special-casing for them here.
    """
    if line.startswith("+"):
        return "green"
    if line.startswith("-"):
        return "red"
    return "dim"


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
        self._active_path: str | None = None
        self._active_args: dict = {}
        self._feed: list[RenderableType] = []
        # Structured record of completed actions for the end-of-turn outcome
        # summary: (verb, path, additions, deletions).
        self._actions: list[tuple[str, str | None, int | None, int | None]] = []
        self._live: Live | None = None

    @property
    def final_text(self) -> str:
        """User-facing answer: real execute/chat text first, else the stripped
        verdict. NEVER the plan echo — the plan has its own panel and is captured
        only so it isn't misclassified as an answer.
        """
        return self._answer_text or self._verdict_text

    def result_summary(self) -> str:
        """The end-of-turn outcome for the Result panel.

        Priority: the model's genuine answer if it produced one; else a concise
        outcome synthesized from the completed actions (e.g.
        ``"Wrote app.py (+3) · Edited calc.py (+1, -1)"``); else the verify
        verdict; else a minimal ``"Done."``. Never the plan echo.
        """
        if self._answer_text.strip():
            return self._answer_text.strip()
        if self._actions:
            summary = self._summarize_actions()
            if summary:
                return summary
        if self._verdict_text.strip():
            return self._verdict_text.strip()
        return "Done."

    def _summarize_actions(self) -> str:
        """One-line outcome from ``self._actions``: mutations if any, else all.

        Repeats of the same path collapse to one entry (latest verb/counts,
        first-seen order). Counts are shown only when both are present and not
        both zero — the same suppression rule as the feed.
        """
        mutations = [a for a in self._actions if a[0] in _MUTATION_VERBS]
        chosen = mutations or self._actions
        by_key: dict[str, tuple[str, str | None, int | None, int | None]] = {}
        order: list[str] = []
        for verb, path, adds, dels in chosen:
            key = path or verb
            if key not in by_key:
                order.append(key)
            by_key[key] = (verb, path, adds, dels)
        parts: list[str] = []
        for key in order:
            verb, path, adds, dels = by_key[key]
            label = f"{verb} {path}" if path else verb
            if adds is not None and dels is not None and (adds or dels):
                label += f" (+{adds})" if dels == 0 else f" (+{adds}, -{dels})"
            parts.append(label)
        return " · ".join(parts)

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
                self._on_tool_end(data)
            elif kind == "on_chat_model_end":
                self._on_model_end(data)
        except Exception:  # noqa: BLE001 — display-only, never break the turn
            logger.debug("live renderer ignored a malformed event", exc_info=True)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def renderable(self) -> RenderableType:
        """Return the live region: completed-action feed, running tool, checklist."""
        lines: list[RenderableType] = []
        # 1) Persistent feed of completed actions (+ any inline diff lines).
        #    Always shown — actions aren't todos, so the /todos toggle never hides
        #    them.
        lines.extend(self._feed)
        # 2) The tool currently running, if any.
        if self._active_tool:
            lines.append(Text(f"running {self._active_tool}...", style="dim"))
        # 3) The live todo checklist — gated by the /todos toggle.
        if self.show_todos:
            for status, text in self._todos:
                marker, style = _STATUS_STYLES.get(status, _STATUS_STYLES["pending"])
                lines.append(Text(f"[{marker}] {text}", style=style))
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
            tool_input = data.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            # Stash the FULL args so the renderer can show the write content / the
            # bash command (not just the path).
            self._active_args = dict(tool_input)
            # deepagents builtins use "file_path"; hcode's own tools use "path".
            path = tool_input.get("file_path") or tool_input.get("path")
            self._active_path = _relative_path(str(path)) if path else None
        self._refresh()

    def _on_tool_end(self, data: dict) -> None:
        """Append the finished tool as a persistent feed entry (+ inline diff).

        Todo tools render via the checklist (``_active_tool`` is never set for
        them), so they add nothing here. Any other tool appends a coloured
        "✓ <verb> <path>" line that stays visible.

        For hcode's edit/write/multi_edit (``response_format="content_and_artifact"``)
        the finished ``ToolMessage`` carries ``.artifact ==
        {diff, additions, deletions, path}``: we read the REAL diff from there
        (never synthesize), render its +/- lines coloured with the ``+++``/``---``/
        ``@@`` header lines dropped, and take the counts straight from the
        artifact. Read-side tools (read_file/ls/…) carry no artifact, so they get
        a plain action line. Output extraction is tolerant — a missing/odd
        output or artifact never raises.
        """
        tool = self._active_tool
        if tool is not None:
            try:
                output = (data or {}).get("output")
                artifact = getattr(output, "artifact", None)
                if not isinstance(artifact, dict):
                    artifact = None
                content = getattr(output, "content", output)
                output_text = content if isinstance(content, str) else ""
                self._append_action(tool, artifact, output_text)
            except Exception:  # noqa: BLE001 — display-only, never break the turn
                logger.debug("live feed ignored a malformed tool result", exc_info=True)
        self._active_tool = None
        self._active_path = None
        self._active_args = {}
        self._refresh()

    def _append_action(
        self, tool: str, artifact: dict | None, output_text: str = ""
    ) -> None:
        """Push a completed-action entry onto the feed.

        A brand-new file write (``Wrote``/``Created`` whose call args still hold
        the full ``content``) and a bash run (``Bash``) each get a multi-line
        block — header + numbered/output body + footer — built from the stashed
        ``self._active_args``. Everything else keeps the compact
        "✓ <verb> <path>" line plus the artifact's real inline diff (used for
        edits and for an overwrite that only carries a diff, not content).
        """
        verb = _TOOL_LABELS.get(tool, tool)
        artifact_path = artifact.get("path") if artifact else None
        path = _relative_path(str(artifact_path)) if artifact_path else self._active_path
        # Counts come straight from the artifact — never recounted from the diff.
        additions = artifact.get("additions") if artifact else None
        deletions = artifact.get("deletions") if artifact else None
        if not isinstance(additions, int):
            additions = None
        if not isinstance(deletions, int):
            deletions = None

        # Structured record for the end-of-turn outcome summary (every branch).
        self._actions.append((verb, path, additions, deletions))

        content = self._active_args.get("content")
        # WRITE — full content in the call args ⇒ numbered preview, not a diff.
        if verb in ("Wrote", "Created") and isinstance(content, str) and content:
            self._feed.append(
                self._write_block(verb, path, content, additions, deletions)
            )
            return

        # BASH — command in the header, captured output as the body.
        if verb == "Bash":
            self._feed.append(self._bash_block(output_text))
            return

        # EDIT — purple icon header above the existing colored diff body.
        if verb == "Edited":
            edit_diff = artifact.get("diff") if artifact else None
            self._feed.append(
                self._edit_block(verb, path, additions, deletions, edit_diff)
            )
            return

        # FALLBACK — compact action line + the artifact's real inline diff.
        label = f"{_DONE_GLYPH} {verb} {path}" if path else f"{_DONE_GLYPH} {verb}"
        diff = artifact.get("diff") if artifact else None
        if artifact and diff and additions is not None and deletions is not None and (additions or deletions):
            label += f" (+{additions})" if deletions == 0 else f" (+{additions}, -{deletions})"

        self._feed.append(Text(label, style="green"))

        if artifact and diff:
            rendered = 0
            for line in str(diff).splitlines():
                if line.startswith(("+++", "---", "@@")):
                    continue  # drop header noise
                if rendered >= _MAX_DIFF_LINES:
                    self._feed.append(Text("… (truncated)", style="dim"))
                    break
                self._feed.append(Text(line, style=_diff_line_style(line)))
                rendered += 1

    def _body_lines(
        self, lines: list[str], render: Callable[[int, str], Text]
    ) -> list[Text]:
        """Build body rows, collapsing the middle when the body is long.

        ``render(idx, line)`` formats the row for the 0-based ORIGINAL line index
        (so line numbers survive truncation). Bodies of ``_BODY_FULL_MAX`` lines
        or fewer render in full; longer ones show the first ``_BODY_HEAD`` and
        last ``_BODY_TAIL`` lines with a dim "… hidden" divider between.
        """
        total = len(lines)
        if total <= _BODY_FULL_MAX:
            return [render(idx, line) for idx, line in enumerate(lines)]
        rows: list[Text] = [render(idx, lines[idx]) for idx in range(_BODY_HEAD)]
        hidden = total - _BODY_HEAD - _BODY_TAIL
        rows.append(Text(f"      ⋮ {hidden} lines hidden", style="dim"))
        rows.extend(render(idx, lines[idx]) for idx in range(total - _BODY_TAIL, total))
        return rows

    def _write_block(
        self,
        verb: str,
        path: str | None,
        content: str,
        additions: int | None,
        deletions: int | None,
    ) -> RenderableType:
        """A WRITE entry: green header, numbered content preview, byte footer."""
        filename = os.path.basename(path) if path else ""
        head = Text(f"{ICON_WRITE} ")
        head.append(f"{verb} {filename}" if filename else verb, style=f"{KPIT_GREEN} bold")
        if path:
            parent = os.path.dirname(path)
            if parent:
                head.append(f" ({parent}/)", style="dim")
        if additions is not None and deletions is not None and (additions or deletions):
            counts = f" (+{additions})" if deletions == 0 else f" (+{additions}, -{deletions})"
            head.append(counts, style=f"{KPIT_GREEN} bold")

        def _num_row(idx: int, line: str) -> Text:
            row = Text()
            row.append(f"  {idx + 1:>3} │ ", style=KPIT_GREEN_DIM)
            row.append(line)
            return row

        rows: list[RenderableType] = [head]
        rows.extend(self._body_lines(content.splitlines(), _num_row))
        nbytes = len(content.encode("utf-8"))
        rows.append(Text(f"  ({nbytes} bytes written)", style="dim"))
        return Group(*rows)

    def _edit_block(
        self,
        verb: str,
        path: str | None,
        additions: int | None,
        deletions: int | None,
        diff: Any,
    ) -> RenderableType:
        """An EDIT entry: purple icon header above the existing colored diff body.

        The header mirrors ``_write_block`` (icon + ``KPIT_PURPLE`` bold verb +
        filename, dim ``(parent/)``, counts); the body is the artifact's real
        diff rendered exactly as the generic fallback does — ``+++``/``---``/``@@``
        dropped, +/- coloured by ``_diff_line_style``, capped at ``_MAX_DIFF_LINES``.
        """
        filename = os.path.basename(path) if path else ""
        head = Text(f"{ICON_EDIT} ")
        head.append(f"{verb} {filename}" if filename else verb, style=f"{KPIT_PURPLE} bold")
        if path:
            parent = os.path.dirname(path)
            if parent:
                head.append(f" ({parent}/)", style="dim")
        if additions is not None and deletions is not None and (additions or deletions):
            counts = f" (+{additions})" if deletions == 0 else f" (+{additions}, -{deletions})"
            head.append(counts, style=f"{KPIT_PURPLE} bold")

        rows: list[RenderableType] = [head]
        if diff:
            rendered = 0
            for line in str(diff).splitlines():
                if line.startswith(("+++", "---", "@@")):
                    continue  # drop header noise
                if rendered >= _MAX_DIFF_LINES:
                    rows.append(Text("… (truncated)", style="dim"))
                    break
                rows.append(Text(line, style=_diff_line_style(line)))
                rendered += 1
        return Group(*rows)

    def _bash_block(self, output_text: str) -> RenderableType:
        """A BASH entry: dim-green "Bash <cmd>" header + dim command output."""
        command = str(self._active_args.get("command", ""))
        if len(command) > _CMD_MAX:
            command = command[:_CMD_MAX] + "…"
        head = Text(f"{ICON_BASH} Bash {command}".rstrip(), style=KPIT_GREEN_DIM)

        rows: list[RenderableType] = [head]
        body = output_text.splitlines() if output_text else []
        if body:
            rows.extend(
                self._body_lines(body, lambda _idx, line: Text(f"  {line}", style="dim"))
            )
        else:
            rows.append(Text("  (no output)", style="dim"))
        return Group(*rows)

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
            # Record the verdict structurally (used to colour the Result panel);
            # do NOT print a stray status line mid-stream.
            self.verify_status = status
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
