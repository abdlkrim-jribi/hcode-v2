"""PEV robustness — keyless proof of the two real gpt-oss failure modes fixed.

Split in two layers, both keyless (the C8 scripted-model pattern — see
tests/helpers/scripted_model.py and docs/verification.md):

* **Unit** — call ``PEVRobustnessMiddleware._intervene`` / the guard helpers on
  hand-built state. Fast, exact, and the only way to prove the false-positive
  GUARDS directly (a scripted model can't be "convinced" by a nudge, so the
  guard logic is what must be pinned down).
* **End-to-end** — drive the REAL ``create_hcode_agent`` graph (real PEV, real
  tools, real routing) with a scripted model, WITH the middleware (default) and,
  for contrast, with ``HCODE_PEV_ROBUSTNESS=0``. This is what proves the
  intervention actually changes the run's outcome — and that a well-behaved
  model's run is byte-identical either way (zero regression).
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from helpers.scripted_model import fresh_scripted_model, full_arc_script

from hcode_v2.agent.pev_robustness import (
    PEVRobustnessMiddleware,
    _looks_like_question,
    _looks_structurally_complete,
    _successful_edit_count,
)

# ── Script fragments ───────────────────────────────────────────────────────────

_EDIT_TURN = {
    "content": "",
    "tool_calls": [{
        "name": "edit",
        "args": {"path": "hello.py", "old_string": "def greet(name):",
                 "new_string": "# greeting helper\ndef greet(name):"},
    }],
}
_HELLO = 'def greet(name):\n    return "hello " + name\n'
_EDIT_MARK = "# greeting helper"


def _question_then_work_script() -> list[dict]:
    # A fast-mode clarifying question, then (after a nudge) real work.
    return [
        {"content": "Which file should I change — hello.py or greeting.py?"},
        _EDIT_TURN,
        {"content": "Done — added the comment."},
    ]


def _vacuous_then_recover_script() -> list[dict]:
    # Claims completion with no edit, then (after a nudge) actually edits.
    return [
        {"content": "1. Add a comment to hello.py.\nPLAN COMPLETE"},
        {"content": "EXECUTION COMPLETE"},          # 0 edits — vacuous
        _EDIT_TURN,                                 # real edit after the nudge
        {"content": "EXECUTION COMPLETE"},          # now legitimate
        {"content": "VERIFIED OK"},
    ]


def _vacuous_no_recover_script() -> list[dict]:
    # The bug shape: plan -> "done" with no edit -> verify passes vacuously.
    return [
        {"content": "1. Add a comment to hello.py.\nPLAN COMPLETE"},
        {"content": "EXECUTION COMPLETE"},          # 0 edits
        {"content": "VERIFIED OK"},                 # would pass over an unchanged tree
    ]


def _markerless_complete_plan_script() -> list[dict]:
    # A real multi-step plan that omits the marker twice, then emits it.
    plan = "Here is the plan:\n1. Read hello.py.\n2. Add a comment above greet."
    return [
        {"content": plan},                          # complete, no marker (attempt 1)
        {"content": plan},                          # still no marker (RETRY -> nudge)
        {"content": plan + "\nPLAN COMPLETE"},       # finally emits it
        _EDIT_TURN,
        {"content": "EXECUTION COMPLETE"},
        {"content": "VERIFIED OK"},
    ]


def _degenerate_plan_script() -> list[dict]:
    # No numbered steps, no marker, does not end with '?': a half response the
    # guard must NOT treat as complete. Repeats (last turn replays).
    return [{"content": "Let me think about how to approach this."}]


# ── E2E harness ────────────────────────────────────────────────────────────────

def _drive(tmp_path, monkeypatch, script, *, task, thread,
           robustness=True, force_plan=False):
    """Run the real agent graph against ``script`` from a fresh hello.py.

    Returns ``(model, result)``. ``robustness=False`` sets the kill-switch so the
    same script runs with the middleware absent — the contrast that proves an
    outcome difference is caused by this middleware and nothing else.
    """
    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(_HELLO, encoding="utf-8")
    model = fresh_scripted_model(script)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: model)
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("HCODE_PEV_ROBUSTNESS", "1" if robustness else "0")
    # The post-edit LSP gate is orthogonal to PEV robustness (a different hook)
    # and a no-op on the clean scripted edit here; disabling it keeps these tests
    # off the pyright subprocess (faster, no language-server dependency in CI).
    # Coverage of the gate itself lives in test_post_edit_lsp_integration.py.
    monkeypatch.setenv("HCODE_POST_EDIT_LSP", "0")

    async def _run():
        agent = await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
            force_plan=force_plan,
        )
        return await agent.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            config={"configurable": {"thread_id": thread}, "recursion_limit": 60},
        )

    return model, asyncio.run(_run())


def _human_texts(result) -> list[str]:
    return [m.content for m in result["messages"]
            if getattr(m, "type", None) == "human" and isinstance(m.content, str)]


def _ai_texts(result) -> list[str]:
    return [m.content for m in result["messages"]
            if getattr(m, "type", None) == "ai" and isinstance(m.content, str)]


# ════════════════════════════════════════════════════════════════════════════
# Unit — the guards (the false-positive protection lives here)
# ════════════════════════════════════════════════════════════════════════════

def _state(phase, messages, iteration=0):
    return {"_pev_phase": phase, "_pev_iteration": iteration, "messages": messages}


def test_intervene_defers_for_tool_calls_and_clean_markers():
    """A turn doing real work, or one carrying any clean PEV marker, is never
    touched — the primary zero-regression guard."""
    mw = PEVRobustnessMiddleware(non_interactive=True)
    tool_turn = AIMessage(content="", tool_calls=[
        {"name": "edit", "args": {}, "id": "e1", "type": "tool_call"}])
    assert mw._intervene(_state("execute", [tool_turn])) is None
    for marker in ("PLAN COMPLETE", "VERIFIED OK", "ISSUES FOUND: x"):
        assert mw._intervene(_state("plan", [AIMessage(content=marker)])) is None


def test_intervene_rejects_vacuous_execution_complete():
    """EXECUTION COMPLETE with zero successful edits is nudged; with a real edit
    it defers to PEV (legitimate completion)."""
    mw = PEVRobustnessMiddleware(non_interactive=True)
    vacuous = _state("execute", [AIMessage(content="all set — EXECUTION COMPLETE")])
    out = mw._intervene(vacuous)
    assert out is not None and out["jump_to"] == "model"
    assert "no file has been edited" in out["messages"][0].content

    # Same marker, but a successful edit landed earlier this task -> defer.
    edited = _state("execute", [
        AIMessage(content="", tool_calls=[
            {"name": "edit", "args": {}, "id": "e1", "type": "tool_call"}]),
        ToolMessage(content="wrote hello.py", tool_call_id="e1"),
        AIMessage(content="EXECUTION COMPLETE"),
    ])
    assert PEVRobustnessMiddleware(non_interactive=True)._intervene(edited) is None


def test_intervene_nudges_clarifying_question_only_when_non_interactive():
    """A trailing-'?' question with no tools/marker is nudged in a non-interactive
    run, left alone in an interactive one, and narration ending with '.' is never
    treated as a question."""
    q = _state("fast", [AIMessage(content="Should I use sqlite or postgres?")])
    out = PEVRobustnessMiddleware(non_interactive=True)._intervene(q)
    assert out is not None and out["jump_to"] == "model"
    assert PEVRobustnessMiddleware(non_interactive=False)._intervene(q) is None

    narration = _state("fast", [AIMessage(content="Should I use X or Y? I'll use X.")])
    assert PEVRobustnessMiddleware(non_interactive=True)._intervene(narration) is None


def test_marker_tolerance_fires_for_complete_plan_on_retry_not_degenerate():
    """The bonus guard: a structurally-complete markerless plan on a RETRY is
    nudged; a degenerate one is not, and neither fires on the first attempt."""
    complete = "1. Read hello.py.\n2. Add a comment above greet."
    degenerate = "Let me think about how to approach this."

    # complete + retry (iteration>=1) -> marker nudge.
    out = PEVRobustnessMiddleware(non_interactive=True)._intervene(
        _state("plan", [AIMessage(content=complete)], iteration=1))
    assert out is not None and "completion marker" in out["messages"][0].content

    # complete but FIRST attempt (iteration 0) -> defer to PEV's own retry.
    assert PEVRobustnessMiddleware(non_interactive=True)._intervene(
        _state("plan", [AIMessage(content=complete)], iteration=0)) is None

    # degenerate + retry -> NOT rescued (false-positive guard holds).
    assert PEVRobustnessMiddleware(non_interactive=True)._intervene(
        _state("plan", [AIMessage(content=degenerate)], iteration=1)) is None


def test_nudges_are_capped_per_task():
    """After _MAX_NUDGES of a kind the middleware defers (returns None) rather
    than looping forever."""
    mw = PEVRobustnessMiddleware(non_interactive=True)
    q = _state("fast", [AIMessage(content="which one?")])
    assert mw._intervene(q) is not None  # 1
    assert mw._intervene(q) is not None  # 2
    assert mw._intervene(q) is None      # capped
    # A fresh task resets the counters.
    mw.before_agent({}, None)
    assert mw._intervene(q) is not None


def test_successful_edit_count_ignores_failed_edits():
    """Only non-error mutating-tool results count as edits."""
    ai = AIMessage(content="", tool_calls=[
        {"name": "edit", "args": {}, "id": "ok", "type": "tool_call"},
        {"name": "edit", "args": {}, "id": "bad", "type": "tool_call"},
        {"name": "read", "args": {}, "id": "r", "type": "tool_call"}])
    messages = [
        ai,
        ToolMessage(content="wrote 3 lines", tool_call_id="ok"),
        ToolMessage(content="Error: file not found", tool_call_id="bad"),
        ToolMessage(content="file body", tool_call_id="r"),  # not a mutating tool
    ]
    assert _successful_edit_count(messages) == 1
    assert _looks_like_question("do this?") and not _looks_like_question("do this.")
    assert _looks_structurally_complete("plan", "1. a\n2. b", []) is True
    assert _looks_structurally_complete("plan", "just prose", []) is False


# ════════════════════════════════════════════════════════════════════════════
# E2E — scenario 1: clarifying question proceeds instead of stalling
# ════════════════════════════════════════════════════════════════════════════

def test_clarifying_question_proceeds_with_middleware(tmp_path, monkeypatch):
    """Fast-mode: a question that would END the run (no tool calls) is nudged to
    proceed, and the real edit then lands. Contrast: with the kill-switch, the
    identical script stalls at the question and the file is never touched."""
    task = "tidy the greeting in hello.py"  # classifier -> fast

    model_on, res_on = _drive(tmp_path, monkeypatch, _question_then_work_script(),
                              task=task, thread="clarify-on", robustness=True)
    assert _EDIT_MARK in (tmp_path / "hello.py").read_text(encoding="utf-8"), \
        "with robustness on, the run should proceed past the question and edit"
    assert model_on.turn >= 2, "the model should have been given a second turn"
    assert any("No user is available" in t for t in _human_texts(res_on)), \
        "the clarification nudge should be recorded in the transcript"

    model_off, _ = _drive(tmp_path, monkeypatch, _question_then_work_script(),
                          task=task, thread="clarify-off", robustness=False)
    assert _EDIT_MARK not in (tmp_path / "hello.py").read_text(encoding="utf-8"), \
        "with robustness off, the question ends the run and nothing is edited"
    assert model_off.turn == 1, "without the nudge the run stalls at the question"


# ════════════════════════════════════════════════════════════════════════════
# E2E — scenario 2: vacuous EXECUTION COMPLETE is not accepted
# ════════════════════════════════════════════════════════════════════════════

def test_vacuous_completion_not_accepted(tmp_path, monkeypatch):
    """execute-phase EXECUTION COMPLETE with 0 edits is rejected: the model is
    forced to actually edit before the arc completes. Contrast: with the
    kill-switch, the same premature 'done' sails straight into a vacuous verify
    over an unchanged file."""
    model_on, res_on = _drive(tmp_path, monkeypatch, _vacuous_then_recover_script(),
                              task="update hello.py", thread="vac-on",
                              robustness=True, force_plan=True)
    assert _EDIT_MARK in (tmp_path / "hello.py").read_text(encoding="utf-8"), \
        "the model was nudged and then really edited the file"
    assert any("no file has been edited" in t for t in _human_texts(res_on)), \
        "the vacuous-completion rejection should be recorded"

    model_off, _ = _drive(tmp_path, monkeypatch, _vacuous_no_recover_script(),
                          task="update hello.py", thread="vac-off",
                          robustness=False, force_plan=True)
    assert _EDIT_MARK not in (tmp_path / "hello.py").read_text(encoding="utf-8"), \
        "without the gate, a 0-edit 'EXECUTION COMPLETE' advances to a vacuous verify"


# ════════════════════════════════════════════════════════════════════════════
# E2E — scenario 3: marker tolerance advances; degenerate does not
# ════════════════════════════════════════════════════════════════════════════

def test_structurally_complete_markerless_plan_advances_via_nudge(tmp_path, monkeypatch):
    """A real plan that omits the marker on a retry gets a targeted marker nudge,
    and the arc then advances to a real edit + completion."""
    _, res = _drive(tmp_path, monkeypatch, _markerless_complete_plan_script(),
                    task="update hello.py", thread="marker-ok",
                    robustness=True, force_plan=True)
    assert _EDIT_MARK in (tmp_path / "hello.py").read_text(encoding="utf-8")
    assert any("completion marker" in t for t in _human_texts(res)), \
        "the marker-tolerance nudge should have fired on the retry"


def test_degenerate_plan_does_not_advance(tmp_path, monkeypatch):
    """A half response with no steps and no marker is NOT rescued: no marker
    nudge fires and the run ends via PEV's honest breaker, never reaching a
    successful edit."""
    _, res = _drive(tmp_path, monkeypatch, _degenerate_plan_script(),
                    task="update hello.py", thread="marker-degen",
                    robustness=True, force_plan=True)
    assert _EDIT_MARK not in (tmp_path / "hello.py").read_text(encoding="utf-8")
    assert not any("completion marker" in t for t in _human_texts(res)), \
        "the marker guard must not fire for a degenerate response"
    assert any("did not complete" in t for t in _ai_texts(res)), \
        "the run should end via PEV's honest non-completion breaker"


# ════════════════════════════════════════════════════════════════════════════
# E2E — scenario 4: zero regression for a well-behaved model
# ════════════════════════════════════════════════════════════════════════════

def test_zero_regression_well_behaved_arc_is_byte_identical(tmp_path, monkeypatch):
    """The canonical well-behaved arc (clean markers, a real edit) produces an
    IDENTICAL message history and turn count with the middleware on vs off, and
    injects zero nudge messages — every robustness check falls through to None."""
    model_on, res_on = _drive(tmp_path, monkeypatch, full_arc_script("hello.py"),
                              task="update hello.py", thread="zr-on",
                              robustness=True, force_plan=True)
    on_seq = [(getattr(m, "type", None), m.content) for m in res_on["messages"]]

    model_off, res_off = _drive(tmp_path, monkeypatch, full_arc_script("hello.py"),
                                task="update hello.py", thread="zr-off",
                                robustness=False, force_plan=True)
    off_seq = [(getattr(m, "type", None), m.content) for m in res_off["messages"]]

    assert on_seq == off_seq, "middleware changed a well-behaved run's messages"
    assert model_on.turn == model_off.turn == 4
    assert not any("No user is available" in c or "no file has been edited" in c
                   or "completion marker" in c
                   for t, c in on_seq if isinstance(c, str)), \
        "no nudge should be injected for a well-behaved model"
    assert _EDIT_MARK in (tmp_path / "hello.py").read_text(encoding="utf-8")
