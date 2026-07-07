"""Harness tool hygiene fixes (audit findings P1 / T1 / T3).

P1 — path contradiction: the vendored FilesystemMiddleware prompt says paths
     must start with "/"; HCode's containment does the opposite. Fixed by
     HarnessNotesMiddleware appending one authoritative correction, landing
     after the vendored prompt text on every call (the last word wins).
T1 — duplicate/shadowed tools: read_file/execute/write_todos duplicate
     HCode's read/bash/todo_read+todo_write under different names (so both
     survive the tool-list merge, unlike ls/glob/grep which HCode's copies
     already win by list order). Fixed via the SAME _ToolExclusionMiddleware
     mechanism already used for edit_file/write_file.
T3 — ask_user/confirm block on input(); in the daemon stdin is the JSON-RPC
     channel. Fixed by excluding them when stdin is not a real TTY.

Uses the SAME kwargs-capture harness as test_factory_tool_routing.py: fake
create_deep_agent/_build_model so no graph compiles and no model/network is
touched — EXCEPT the end-to-end test at the bottom, which fakes only the chat
model (mirroring libs/deepagents/tests/unit_tests/chat_model.py's
GenericFakeChatModel.bind_tools -> self pattern) and lets the REAL
create_deep_agent compile and run the REAL graph, proving the new middleware
(HarnessNotesMiddleware + the expanded exclusion set) doesn't break real
compilation or a real turn — not just that a mocked capture accepts the kwargs.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage

from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from hcode_v2.agent import factory
from hcode_v2.agent.harness_notes import HarnessNotesMiddleware

# Vendored builtins that duplicate an HCode tool under a DIFFERENT name (no
# dict-collision auto-resolution — both would otherwise survive and reach
# the model). Must be excluded.
_DISTINCT_NAME_DUPLICATES = ("read_file", "execute", "write_todos")
# ls/glob/grep share an EXACT name with HCode's own tools; the merge order
# already makes HCode's copy win, so these must NEVER be excluded (excluding
# them would delete the sole survivor, not "pick a side").
_SHARED_NAME_TOOLS_NOT_EXCLUDED = ("ls", "glob", "grep")
# HCode's own replacements — must remain bound and never excluded.
_HCODE_REPLACEMENTS = ("read", "bash", "todo_read", "todo_write")


def _excluded_tool_names(middleware: list) -> set[str]:
    names: set[str] = set()
    for mw in middleware:
        if isinstance(mw, _ToolExclusionMiddleware):
            names |= set(getattr(mw, "_excluded", frozenset()))
    return names


def _capture_create_deep_agent_kwargs(monkeypatch, tmp_path: Path) -> dict:
    """Build the agent with create_deep_agent/_build_model faked; return the
    kwargs the factory passed to create_deep_agent (mirrors test_factory_tool_routing.py)."""
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: object())
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "workflows").mkdir(exist_ok=True)

    async def _build() -> None:
        await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_such_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
        )

    asyncio.run(_build())
    return captured


# ── T1: duplicate/shadowed tools ────────────────────────────────────────────

def test_distinct_name_duplicates_are_excluded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)  # keep ask_user/confirm out of scope here
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    excluded = _excluded_tool_names(captured["middleware"])

    for name in _DISTINCT_NAME_DUPLICATES:
        assert name in excluded, f"vendored duplicate '{name}' is not excluded — the model can still call it"


def test_shared_name_tools_are_never_excluded(monkeypatch, tmp_path: Path) -> None:
    # Excluding ls/glob/grep would delete the ALREADY-deduplicated survivor
    # (HCode's own — it wins the merge-order dict collision), not choose a side.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    excluded = _excluded_tool_names(captured["middleware"])

    for name in _SHARED_NAME_TOOLS_NOT_EXCLUDED:
        assert name not in excluded, f"'{name}' must never be excluded — only one copy survives the merge"


def test_hcode_replacements_are_bound_and_never_excluded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    names = {getattr(t, "name", None) for t in captured["tools"]}
    excluded = _excluded_tool_names(captured["middleware"])

    for name in _HCODE_REPLACEMENTS:
        assert name in names, f"hcode's '{name}' tool is not bound to the agent"
        assert name not in excluded, f"hcode's '{name}' tool is wrongly excluded"


def test_pre_existing_edit_write_exclusions_still_present(monkeypatch, tmp_path: Path) -> None:
    # Regression guard: the ORIGINAL exclusion pair (from the earlier
    # edit_file/write_file fix) must survive this change unchanged.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    excluded = _excluded_tool_names(captured["middleware"])

    assert "edit_file" in excluded
    assert "write_file" in excluded


# ── T3: ask_user/confirm gated by TTY detection ─────────────────────────────

def test_ask_user_confirm_excluded_when_not_a_tty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)  # simulate the daemon's piped stdin
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    excluded = _excluded_tool_names(captured["middleware"])

    assert "ask_user" in excluded, "ask_user must be excluded in a non-interactive (daemon) run"
    assert "confirm" in excluded, "confirm must be excluded in a non-interactive (daemon) run"


def test_ask_user_confirm_kept_when_tty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)  # simulate the CLI's real terminal
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    excluded = _excluded_tool_names(captured["middleware"])
    names = {getattr(t, "name", None) for t in captured["tools"]}

    assert "ask_user" not in excluded, "ask_user must stay available in the interactive CLI"
    assert "confirm" not in excluded, "confirm must stay available in the interactive CLI"
    assert "ask_user" in names and "confirm" in names  # still bound, not removed from the registry


def test_is_interactive_stdin_reflects_isatty(monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert factory._is_interactive_stdin() is True
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert factory._is_interactive_stdin() is False


def test_is_interactive_stdin_survives_isatty_error(monkeypatch) -> None:
    # A closed/replaced stdin (e.g. some test runners) can raise instead of
    # returning False — must never crash agent construction.
    def _boom():
        raise ValueError("no stdin")
    monkeypatch.setattr(sys.stdin, "isatty", _boom)
    assert factory._is_interactive_stdin() is False


# ── P1: HarnessNotesMiddleware is wired in and appends after the request ───

def test_harness_notes_middleware_is_installed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    notes = [m for m in captured["middleware"] if isinstance(m, HarnessNotesMiddleware)]
    assert notes, "factory installs no HarnessNotesMiddleware — the path contradiction is unaddressed"


def test_harness_notes_note_states_one_path_convention(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    note = next(m for m in captured["middleware"] if isinstance(m, HarnessNotesMiddleware))._note

    assert "must start with" not in note.lower() or "ignore" in note.lower()
    assert "relative" in note.lower()
    # Also corrects the dangling references left by the T1 exclusions.
    assert "read_file" in note and "execute" in note and "write_todos" in note
    assert "`read`" in note and "`bash`" in note


# ── HarnessNotesMiddleware: direct unit test of the append mechanism ───────
# Mirrors libs/deepagents/tests/unit_tests/middleware/test_pev_middleware.py's
# own ModelRequest construction — the same pattern PEV's phase-prompt tests use.

def _make_model_request(system_message=None) -> ModelRequest:
    from unittest.mock import MagicMock
    runtime = MagicMock()
    runtime.context = {}
    runtime.store = None
    del runtime.config
    model = MagicMock()
    model._llm_type = "test-model"
    return ModelRequest(
        model=model, messages=[], system_message=system_message,
        runtime=runtime, state={},
    )


def _system_text(request: ModelRequest) -> str:
    sm = request.system_message
    if sm is None:
        return ""
    return " ".join(b.get("text", "") for b in sm.content_blocks if b.get("type") == "text")


def test_wrap_model_call_appends_note_after_existing_system_message() -> None:
    from langchain_core.messages import SystemMessage

    mw = HarnessNotesMiddleware("MY NOTE")
    existing = SystemMessage(content_blocks=[{"type": "text", "text": "EARLIER FS PROMPT"}])
    request = _make_model_request(system_message=existing)
    captured = []

    def handler(req: ModelRequest) -> AIMessage:
        captured.append(req)
        return AIMessage(content="ok")

    mw.wrap_model_call(request, handler)

    text = _system_text(captured[0])
    assert "EARLIER FS PROMPT" in text
    assert "MY NOTE" in text
    # The note must come AFTER the earlier text — "the last word wins".
    assert text.index("EARLIER FS PROMPT") < text.index("MY NOTE")


def test_wrap_model_call_works_with_no_prior_system_message() -> None:
    mw = HarnessNotesMiddleware("SOLO NOTE")
    request = _make_model_request(system_message=None)
    captured = []

    mw.wrap_model_call(request, lambda req: captured.append(req) or AIMessage(content="ok"))

    assert "SOLO NOTE" in _system_text(captured[0])


@pytest.mark.asyncio
async def test_awrap_model_call_appends_note() -> None:
    from langchain_core.messages import SystemMessage

    mw = HarnessNotesMiddleware("ASYNC NOTE")
    existing = SystemMessage(content_blocks=[{"type": "text", "text": "BASE"}])
    request = _make_model_request(system_message=existing)
    captured = []

    async def handler(req: ModelRequest) -> AIMessage:
        captured.append(req)
        return AIMessage(content="ok")

    await mw.awrap_model_call(request, handler)

    text = _system_text(captured[0])
    assert "BASE" in text and "ASYNC NOTE" in text
    assert text.index("BASE") < text.index("ASYNC NOTE")


# ── VERIFY #4: the REAL agent still builds and runs a task end-to-end ──────
# Only the chat model is faked (no network); create_deep_agent, the full
# middleware stack (PEV, SafetyGuard, Skills, Workflow, the expanded
# _ToolExclusionMiddleware, HarnessNotesMiddleware, _PathContainmentMiddleware),
# and the compiled LangGraph all run for real.

class _OneShotFakeModel:
    """Minimal BaseChatModel: bind_tools -> self, always answers with no tool
    calls (mirrors GenericFakeChatModel.bind_tools's well-established pattern).
    A response with no tool_calls ends the turn cleanly — the simplest
    possible full round-trip through every middleware in the real stack.
    """

    def __init__(self) -> None:
        from langchain_core.language_models import BaseChatModel

        class _Impl(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "one-shot-fake"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                from langchain_core.outputs import ChatGeneration, ChatResult
                return ChatResult(generations=[
                    ChatGeneration(message=AIMessage(content="Hello! Task done."))
                ])

        self._impl = _Impl()

    def __call__(self):
        return self._impl


def test_agent_builds_and_runs_trivial_task_end_to_end(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: _OneShotFakeModel()())
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "workflows").mkdir(exist_ok=True)

    from langchain_core.messages import HumanMessage

    async def _build_and_run():
        agent = await factory.create_hcode_agent(
            persist=False,  # MemorySaver — no sqlite file on disk
            mcp_config=str(tmp_path / "no_such_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
        )
        # "say hello" classifies as PEV phase "fast" (TaskClassifier) — no
        # marker discipline required, the simplest full round trip.
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="say hello")]},
            config={"configurable": {"thread_id": "e2e-test"}, "recursion_limit": 1000},
        )

    result = asyncio.run(_build_and_run())

    messages = result["messages"]
    assert messages, "the real compiled graph produced no messages"
    last_ai = next(m for m in reversed(messages) if isinstance(m, AIMessage))
    assert "Hello! Task done." in last_ai.content
