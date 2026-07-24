"""Deterministic scripted ChatModel — drives the REAL agent pipeline, keyless.

## The C8 pattern

This project's mocks (the frontend fixtures, `server.py::_mock_streaming_task`)
SIMULATE event outcomes — they emit `plan_created`, `file_patch`, `lsp_verify:*`
etc. directly, without any of the machinery that's supposed to produce them
actually running. That is exactly why five separate features (work_dir
forwarding, skills forwarding, the native query correlator, plan-review,
the native desktop delivery pass) passed mock-driven checks and then failed
for real — the mock hides bugs in the machinery it's standing in for. See
`docs/verification.md` for the full rationale.

``ScriptedChatModel`` takes the opposite approach: it is a real
``BaseChatModel`` that returns a fixed, ordered sequence of responses, and is
wired in at the ONE seam where a real API call would otherwise happen
(``factory._build_model``). Everything else is real: the actual
``create_hcode_agent`` graph, the actual middleware stack (PEV phase
transitions, PlanReviewMiddleware's interrupt, PromptSlimMiddleware, the
post-edit LSP gate), the actual tools (a scripted ``edit`` tool_call really
writes to disk; a scripted ``check_diagnostics`` call really runs pyright),
and — when driven through ``scripts/probe_daemon.py`` with
``HCODE_FAKE_MODEL`` — the actual daemon process, JSON-RPC transport, and
event-emission path (`StreamingBridge`, `bridge.py`). Nothing about the
pipeline is simulated except which tokens the "model" happens to say next.

This is what makes it CI-runnable: no API key, no network call, no quota, no
flakiness from a real provider — but a REAL bug in PEV's phase machinery, the
plan-review interrupt, the post-edit gate, or the event bridge will still
surface, because all of that code actually executes.

## Two ways to use it

1. **In-process (pytest)** — ``fresh_scripted_model(script)`` returns a fresh
   ``ScriptedChatModel`` instance bound to your script; monkeypatch
   ``factory._build_model`` to return it, then drive
   ``create_hcode_agent(...).ainvoke(...)`` directly. See
   ``test_post_edit_lsp_integration.py`` for the pattern this module was
   extracted from (kept working, now sharing this implementation instead of
   duplicating it per test file).

2. **Cross-process (the real daemon, via ``scripts/probe_daemon.py``)** — a
   scripted-turn sequence is written to a JSON file and the daemon subprocess
   is launched with ``HCODE_FAKE_MODEL=<path>`` (+ ``HCODE_ALLOW_FAKE=1`` — see
   the two-var guard in ``factory.py``, so this can never activate by
   accident). ``load_script()`` reads that file; the daemon-side hook builds
   a ``ScriptedChatModel`` from it instead of a real provider client. This is
   what lets ``probe_daemon.py`` run a full raw-JSON-RPC probe against a REAL,
   SEPARATE daemon process with zero API dependency — the CI-safe form of
   this project's own verification rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, PrivateAttr

# A script is an ordered list of turns. Each turn:
#   {"content": str, "tool_calls": [{"name": str, "args": dict, "id"?: str}]}
# "tool_calls" is optional (omit or [] for a plain text turn). Turn N is
# replayed for the model's Nth call; the LAST turn repeats if more calls
# happen than the script has entries (so an under-scripted script degrades to
# "keep answering with the final turn" rather than raising).
Script = list[dict[str, Any]]


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed sequence of AIMessage turns; records every call's input.

    Script/turn/call-log live in ``PrivateAttr``s (pydantic v2's supported way
    to carry mutable per-INSTANCE state on a BaseModel — the same pattern
    ``ResilientChatModel._active`` already uses in provider/resilient.py), so
    each instance is independently isolated with no dynamic-subclass tricks.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _script: Script = PrivateAttr(default_factory=list)
    _calls: list[list[Any]] = PrivateAttr(default_factory=list)
    _turn: int = PrivateAttr(default=0)

    def __init__(self, script: Script | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._script = list(script or [])
        self._calls = []
        self._turn = 0

    @property
    def script(self) -> Script:
        return self._script

    @property
    def calls(self) -> list[list[Any]]:
        return self._calls

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def _llm_type(self) -> str:
        return "hcode-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        # Tools are executed by the real graph; the script itself decides which
        # tool_calls to emit and when, so binding is a no-op.
        return self

    def _next_message(self) -> AIMessage:
        if not self._script:
            return AIMessage(content="(scripted model: empty script)")
        idx = min(self._turn, len(self._script) - 1)
        entry = self._script[idx]
        tool_calls = []
        for i, tc in enumerate(entry.get("tool_calls") or []):
            tool_calls.append({
                "name": tc["name"],
                "args": tc.get("args", {}),
                "id": tc.get("id") or f"tc-{idx}-{i}",
                "type": "tool_call",
            })
        return AIMessage(content=entry.get("content", ""), tool_calls=tool_calls)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._calls.append(list(messages))
        msg = self._next_message()
        self._turn += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._generate(messages, stop=stop, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        # Overriding _stream (even as a single-chunk generator) is what makes
        # LangChain treat this model as streaming-capable at all: BaseChatModel
        # ._should_stream() checks whether _stream/_astream were overridden, and
        # if NEITHER is, .astream()/.agenerate() SKIP the token-callback path
        # entirely and call ainvoke() directly — no on_llm_new_token, so
        # astream_events never emits on_chat_model_stream. Since bridge.py's PEV
        # phase-marker detection (plan_created/execution_started/
        # verification_started) is wired ONLY to on_chat_model_stream, a
        # non-streaming fake model would silently make those UI events vanish
        # while the underlying graph still completes correctly — a real gap
        # this probe infrastructure is meant to catch, so the fake model must
        # behave like a real (streaming) provider, not skip that mechanism.
        self._calls.append(list(messages))
        msg = self._next_message()
        self._turn += 1
        chunk = AIMessageChunk(content=msg.content, tool_calls=msg.tool_calls)
        yield ChatGenerationChunk(message=chunk)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self._stream(messages, stop=stop, **kwargs):
            yield chunk


def fresh_scripted_model(script: Script) -> ScriptedChatModel:
    """A new ``ScriptedChatModel`` instance bound to ``script`` — a thin,
    readable alias for ``ScriptedChatModel(script)`` used throughout the tests
    and the probe so call sites read as "give me a fresh model for this
    script" rather than exposing the constructor directly."""
    return ScriptedChatModel(script=script)


def load_script(path: str | Path) -> Script:
    """Load a scripted turn sequence from a JSON file (the cross-process form —
    see the module docstring's "Cross-process" section)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_script(script: Script, path: str | Path) -> None:
    """Write a scripted turn sequence to a JSON file, for use with
    ``HCODE_FAKE_MODEL``. Mirror of ``load_script``."""
    Path(path).write_text(json.dumps(script, indent=2), encoding="utf-8")


# ── Canned scripts for the common PEV-arc shapes ───────────────────────────────
#
# PEV's phase machinery (vendored pev.py, read not edited) advances purely on
# text markers in the model's own output — PLAN COMPLETE / EXECUTION COMPLETE /
# VERIFIED OK — so a script that emits them in order deterministically drives
# the full plan -> execute -> verify arc, independent of which phase actually
# bound which tools (the plan phase strips tools; a scripted model doesn't care
# either way, it just replays the next turn).

def full_arc_script(edit_path: str = "hello.py") -> Script:
    """plan (no tools, marker only) -> execute (one edit) -> EXECUTION COMPLETE
    -> verify (VERIFIED OK). Drives PEV through every phase transition."""
    return [
        {"content": "1. Add a comment to the file.\nPLAN COMPLETE"},
        {"content": "", "tool_calls": [{
            "name": "edit",
            "args": {"path": edit_path, "old_string": "def greet(name):",
                     "new_string": "# greeting helper\ndef greet(name):"},
        }]},
        {"content": "EXECUTION COMPLETE"},
        {"content": "VERIFIED OK"},
    ]


def no_arc_script() -> Script:
    """A single closing turn, no tool calls — the "fast" phase shape (no
    PEV markers at all; the classifier never routes here into plan/verify)."""
    return [{"content": "Done — nothing to change."}]


__all__ = [
    "Script",
    "ScriptedChatModel",
    "fresh_scripted_model",
    "load_script",
    "save_script",
    "full_arc_script",
    "no_arc_script",
]
