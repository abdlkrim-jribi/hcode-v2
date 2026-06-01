"""Streaming bridge: LangGraph astream_events v2 → HcodeMessage notifications.

Maps raw LangGraph events from ``agent.astream_events(..., version="v2")`` into
the HcodeMessage event shapes the React UI expects (defined in agent-events.ts).

Usage
-----
    bridge = StreamingBridge(emit_fn=daemon.emit_event, pev_mode=True)
    async for event in agent.astream_events(input_, config=cfg, version="v2"):
        bridge.process_event(event)
    bridge.finalize(last_message_text)

``emit_fn`` receives ``(type_: str, payload: dict | None)`` — it should call
``daemon.emit_event`` which writes the JSON notification to stdout.

LangGraph event → HcodeMessage mapping
---------------------------------------
on_chat_model_stream  (text token)          → streaming_chunk {content, phase}
on_tool_start                               → task_update    {markdown, step="tool:<name>"}
on_tool_end                                 → task_update    {markdown, step="tool_result:<name>"}
token buffer contains "PLAN COMPLETE"       → plan_created   {markdown, implementationPlanMd, timestamp}
                                              execution_started {timestamp}
token buffer contains "EXECUTION COMPLETE"  → verification_started {timestamp}
(PEV trivial / fast — no markers seen)      → (no phase events, just tokens + tools)

Phase detection is text-based, matching PEVMiddleware's own detection logic
(looks for "PLAN COMPLETE" / "EXECUTION COMPLETE" in the accumulated AI message).
This avoids relying on LangGraph private-channel state keys (_pev_phase).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Tools that write files — emit task_update with file info
_FILE_TOOLS: frozenset[str] = frozenset({"write", "edit", "multi_edit"})


def _now() -> int:
    return int(time.time() * 1000)


def _text_from_content(content: Any) -> str:
    """Normalize AIMessageChunk.content to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return ""


class StreamingBridge:
    """Stateful mapper from LangGraph event dicts to HcodeMessage notifications.

    One instance per ``run_task`` invocation; do not reuse across tasks.
    """

    def __init__(
        self,
        emit_fn: Callable[[str, dict | None], None],
        pev_mode: bool = True,
    ) -> None:
        self._emit = emit_fn
        self._pev_mode = pev_mode

        # Phase tracking (text-marker based)
        self._current_phase: str = "plan" if pev_mode else "fast"
        self._plan_done: bool = False
        self._exec_done: bool = False

        # Accumulate AI message tokens to detect phase-transition markers
        self._token_buffer: str = ""

        # Active tool call — set on on_tool_start, cleared on on_tool_end
        self._active_tool: str | None = None
        self._active_tool_input: dict | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def on_task_start(self) -> None:
        """Call once before the first process_event — emits planning_started."""
        if self._pev_mode:
            self._emit("planning_started", {"timestamp": _now()})

    def process_event(self, event: dict) -> None:
        """Dispatch one raw LangGraph astream_events event."""
        kind: str = event.get("event", "")
        name: str = event.get("name", "")
        data: dict = event.get("data") or {}

        try:
            if kind == "on_chat_model_stream":
                self._handle_token(data)
            elif kind == "on_tool_start":
                self._handle_tool_start(name, data)
            elif kind == "on_tool_end":
                self._handle_tool_end(name, data)
            # on_chain_*, on_chat_model_start/end, on_retriever_* — not mapped in C2
        except Exception:
            logger.exception("bridge error processing event kind=%s name=%s", kind, name)

    def finalize(self, summary: str = "") -> None:
        """Call after the stream is exhausted — emits the terminal done event."""
        self._emit("done", {"summary": summary, "timestamp": _now()})

    # ── Token handling ────────────────────────────────────────────────────────

    def _handle_token(self, data: dict) -> None:
        chunk = data.get("chunk")
        if chunk is None:
            return
        text = _text_from_content(getattr(chunk, "content", ""))
        if not text:
            return

        self._token_buffer += text
        self._emit("streaming_chunk", {"content": text, "phase": self._current_phase})
        self._check_phase_markers()

    def _check_phase_markers(self) -> None:
        upper = self._token_buffer.upper()

        if (
            self._pev_mode
            and not self._plan_done
            and "PLAN COMPLETE" in upper
        ):
            self._plan_done = True
            plan_text = self._token_buffer.strip()
            self._emit("plan_created", {
                "markdown": plan_text,
                "taskMd": plan_text,
                "implementationPlanMd": plan_text,
                "timestamp": _now(),
            })
            self._current_phase = "execute"
            self._emit("execution_started", {"timestamp": _now()})
            self._token_buffer = ""

        elif (
            self._pev_mode
            and self._plan_done
            and not self._exec_done
            and "EXECUTION COMPLETE" in upper
        ):
            self._exec_done = True
            self._current_phase = "verify"
            self._emit("verification_started", {"timestamp": _now()})
            self._token_buffer = ""

    # ── Tool handling ─────────────────────────────────────────────────────────

    def _handle_tool_start(self, name: str, data: dict) -> None:
        tool_input = data.get("input") or {}
        self._active_tool = name
        self._active_tool_input = tool_input if isinstance(tool_input, dict) else {}

        path = self._active_tool_input.get("path", "")
        desc = f"`{name}`"
        if path:
            desc += f" → `{path}`"
        self._emit("task_update", {
            "markdown": f"**Running tool:** {desc}",
            "step": f"tool:{name}",
        })

    def _handle_tool_end(self, name: str, data: dict) -> None:
        self._emit("task_update", {
            "markdown": f"**Tool done:** `{name}`",
            "step": f"tool_result:{name}",
        })
        self._active_tool = None
        self._active_tool_input = None
