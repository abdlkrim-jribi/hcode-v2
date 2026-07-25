"""Integration proof for the post-edit LSP gate: the full error→fix loop through
the REAL agent machinery — real create_hcode_agent graph, real middlewares, real
file tools writing a real file, real pyright — driven by a deterministic
scripted model (no API key, no network, no mock events).

This is the C8 pattern (see tests/helpers/scripted_model.py + docs/verification.md):
the frontend/daemon mocks simulate event OUTCOMES and would hide gate bugs; a
scripted MODEL instead drives the production pipeline and lets us assert what
the model actually RECEIVES — including the gate's addendum inside the edit
tool's result.

Skipped cleanly when pyright is not installed (the gate itself no-ops there,
which the unit tests already cover).
"""

from __future__ import annotations

import asyncio

import pytest

from hcode_v2.lsp import lsp_available
from helpers.scripted_model import fresh_scripted_model

pytestmark = pytest.mark.skipif(
    not lsp_available("python"), reason="pyright not installed — gate no-ops"
)

BROKEN = (
    'def greet(name):\n    return "hello " + name\n\n'
    'def shout(text: str) -> int:\n    return text.upper()\n'
)
GATE_MARKER = "Language server check (post-edit)"


def test_gate_error_fix_loop_through_real_agent(tmp_path, monkeypatch):
    """rename (fast mode, no arc) → gate appends real pyright errors to the tool
    result → scripted fix lands → gate goes clean → file ends type-correct."""
    from langchain_core.messages import HumanMessage, ToolMessage

    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(BROKEN, encoding="utf-8")

    # Turn 1: rename shout -> yell (keeps the pre-existing bad annotation).
    # Turn 2: the gate should have appended pyright's error to the rename
    #   result — fix the annotation. The script is UNCONDITIONAL (it doesn't
    #   read the tool result to decide) — the test itself asserts the
    #   addendum really was in this turn's input.
    # Turn 3+: done (fast phase ends on a no-tool-call turn).
    script = [
        {"content": "", "tool_calls": [{
            "name": "edit", "id": "tc-rename",
            "args": {"path": "hello.py",
                     "old_string": "def shout(text: str) -> int:",
                     "new_string": "def yell(text: str) -> int:"},
        }]},
        {"content": "", "tool_calls": [{
            "name": "edit", "id": "tc-fix",
            "args": {"path": "hello.py",
                     "old_string": "def yell(text: str) -> int:",
                     "new_string": "def yell(text: str) -> str:"},
        }]},
        {"content": "Renamed shout to yell and fixed the return annotation."},
    ]
    model = fresh_scripted_model(script)
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
    calls = model.calls
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
    assert model.turn == 3


def test_gate_clean_edit_adds_no_model_turns(tmp_path, monkeypatch):
    """A clean edit through the real agent: gate runs, appends nothing, and the
    turn count equals the no-gate baseline (edit + closing text = 2 turns)."""
    from langchain_core.messages import HumanMessage, ToolMessage

    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(
        'def greet(name):\n    return "hello " + name\n', encoding="utf-8"
    )

    script = [
        {"content": "", "tool_calls": [{
            "name": "edit", "id": "tc-c",
            "args": {"path": "hello.py",
                     "old_string": "def greet(name):",
                     "new_string": "# greeting helper\ndef greet(name):"},
        }]},
        {"content": "Added the comment."},
    ]
    model = fresh_scripted_model(script)
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
    calls = model.calls
    # Baseline shape: turn 1 = the edit, turn 2 = closing text. Nothing more.
    assert model.turn == 2
    # And the edit's result reached the model UNPOLLUTED (gate ran, found clean,
    # appended nothing).
    turn2_tools = [m for m in calls[1] if isinstance(m, ToolMessage)]
    assert turn2_tools and GATE_MARKER not in turn2_tools[-1].content
