"""Integration proof for the post-edit LSP gate: the full error→fix loop through
the REAL agent machinery — real create_hcode_agent graph, real middlewares, real
file tools writing a real file, real pyright — driven by a deterministic
scripted model (no API key, no network, no mock events).

This is the C8 pattern from the master plan: the frontend/daemon mocks simulate
event OUTCOMES and would hide gate bugs; a scripted MODEL instead drives the
production pipeline and lets us assert what the model actually RECEIVES —
including the gate's addendum inside the edit tool's result.

Skipped cleanly when pyright is not installed (the gate itself no-ops there,
which the unit tests already cover).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hcode_v2.lsp import lsp_available

pytestmark = pytest.mark.skipif(
    not lsp_available("python"), reason="pyright not installed — gate no-ops"
)

BROKEN = (
    'def greet(name):\n    return "hello " + name\n\n'
    'def shout(text: str) -> int:\n    return text.upper()\n'
)
GATE_MARKER = "Language server check (post-edit)"


def _scripted_model(work_dir: str):
    """A BaseChatModel that replays scripted tool calls and records its inputs."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ScriptedChatModel(BaseChatModel):
        calls: list = []          # input message lists, one per model turn
        turn: int = 0

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools: Any, **kwargs: Any):
            return self  # tools are executed by the graph, not the model

        def _script(self) -> AIMessage:
            # NB: BaseChatModel is a pydantic model — `self.turn` would read the
            # INSTANCE field (frozen at 0); the counters live on the CLASS.
            turn = type(self).turn
            # Turn 1: rename shout -> yell (keeps the pre-existing bad annotation).
            if turn == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "edit", "id": "tc-rename", "type": "tool_call",
                        "args": {"path": "hello.py",
                                 "old_string": "def shout(text: str) -> int:",
                                 "new_string": "def yell(text: str) -> int:"},
                    }],
                )
            # Turn 2: the gate should have appended pyright's error to the rename
            # result — fix the annotation. (The test asserts the addendum really
            # was in this turn's input; the script itself is unconditional.)
            if turn == 2:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "edit", "id": "tc-fix", "type": "tool_call",
                        "args": {"path": "hello.py",
                                 "old_string": "def yell(text: str) -> int:",
                                 "new_string": "def yell(text: str) -> str:"},
                    }],
                )
            # Turn 3+: done (fast phase ends on a no-tool-call turn).
            return AIMessage(content="Renamed shout to yell and fixed the return annotation.")

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            type(self).turn += 1
            type(self).calls.append(list(messages))
            return ChatResult(generations=[ChatGeneration(message=self._script())])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return self._generate(messages, stop=stop, **kwargs)

    ScriptedChatModel.calls = []
    ScriptedChatModel.turn = 0
    return ScriptedChatModel()


def test_gate_error_fix_loop_through_real_agent(tmp_path, monkeypatch):
    """rename (fast mode, no arc) → gate appends real pyright errors to the tool
    result → scripted fix lands → gate goes clean → file ends type-correct."""
    from langchain_core.messages import HumanMessage, ToolMessage

    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(BROKEN, encoding="utf-8")
    model = _scripted_model(str(tmp_path))
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: model)
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))     # restore-guard the global
    monkeypatch.delenv("HCODE_POST_EDIT_LSP", raising=False)  # default ON

    async def _run():
        agent = await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
        )
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="In hello.py, rename the function shout to yell.")]},
            config={"configurable": {"thread_id": "gate-int-1"}, "recursion_limit": 50},
        )

    asyncio.run(_run())

    # ── The file: rename applied AND annotation fixed → pyright-clean result.
    final = (tmp_path / "hello.py").read_text(encoding="utf-8")
    assert "def yell(text: str) -> str:" in final
    assert "shout" not in final

    # ── The loop mechanics, from what the model actually received:
    calls = type(model).calls
    assert len(calls) >= 3, f"expected >=3 model turns, got {len(calls)}"

    def tool_messages(msgs):
        return [m for m in msgs if isinstance(m, ToolMessage)]

    # Turn 2's input: the rename's ToolMessage carries the gate's addendum with
    # the REAL pyright diagnostic for the bad annotation.
    turn2_tools = tool_messages(calls[1])
    assert turn2_tools, "turn 2 saw no tool result"
    rename_result = turn2_tools[-1].content
    assert GATE_MARKER in rename_result
    assert "ERROR" in rename_result
    assert "str" in rename_result and "int" in rename_result  # the actual type clash

    # Turn 3's input: the fix's ToolMessage is CLEAN — no addendum appended.
    turn3_tools = tool_messages(calls[2])
    assert turn3_tools, "turn 3 saw no tool result"
    assert GATE_MARKER not in turn3_tools[-1].content

    # ── Fast mode really stayed fast: exactly the scripted 3 turns — the two
    # edits + the closing text. No plan/verify model round-trips were added.
    assert type(model).turn == 3


def test_gate_clean_edit_adds_no_model_turns(tmp_path, monkeypatch):
    """A clean edit through the real agent: gate runs, appends nothing, and the
    turn count equals the no-gate baseline (edit + closing text = 2 turns)."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(
        'def greet(name):\n    return "hello " + name\n', encoding="utf-8"
    )
    model = _scripted_model(str(tmp_path))

    # Rescript: one comment edit, then done.
    def _script(self):
        if type(model).turn == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "edit", "id": "tc-c", "type": "tool_call",
                    "args": {"path": "hello.py",
                             "old_string": "def greet(name):",
                             "new_string": "# greeting helper\ndef greet(name):"},
                }],
            )
        return AIMessage(content="Added the comment.")

    type(model)._script = _script
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: model)
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    monkeypatch.delenv("HCODE_POST_EDIT_LSP", raising=False)

    async def _run():
        agent = await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
        )
        await agent.ainvoke(
            {"messages": [HumanMessage(content="add a comment to hello.py")]},
            config={"configurable": {"thread_id": "gate-int-2"}, "recursion_limit": 50},
        )

    asyncio.run(_run())

    assert "# greeting helper" in (tmp_path / "hello.py").read_text(encoding="utf-8")
    calls = type(model).calls
    # Baseline shape: turn 1 = the edit, turn 2 = closing text. Nothing more.
    assert type(model).turn == 2
    # And the edit's result reached the model UNPOLLUTED (gate ran, found clean,
    # appended nothing).
    turn2_tools = [m for m in calls[1] if isinstance(m, ToolMessage)]
    assert turn2_tools and GATE_MARKER not in turn2_tools[-1].content
