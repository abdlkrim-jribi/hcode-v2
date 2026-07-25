"""In-process proof that a scripted model can drive the FULL PEV arc and the
plan-review interrupt through the real agent graph — keyless, no mock events.

This is the same C8 pattern as test_post_edit_lsp_integration.py (see
tests/helpers/scripted_model.py), applied to the two scenarios
scripts/probe_daemon.py exercises cross-process against the real daemon:
the full plan->execute->verify arc, and plan-review's pause/accept. Proving
both work in-process first de-risks the daemon-subprocess wiring — if PEV's
phase machinery or PlanReviewMiddleware's interrupt didn't fire correctly
against a scripted model, it would show up here, fast, without a subprocess.
"""

from __future__ import annotations

import asyncio

from helpers.scripted_model import fresh_scripted_model, full_arc_script


def test_scripted_model_drives_full_pev_arc(tmp_path, monkeypatch):
    """force_plan=True + a marker-emitting script -> plan -> execute (real edit)
    -> verify, all through the REAL PEVMiddleware phase machinery."""
    from langchain_core.messages import HumanMessage

    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(
        'def greet(name):\n    return "hello " + name\n', encoding="utf-8"
    )
    model = fresh_scripted_model(full_arc_script("hello.py"))
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: model)
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))

    async def _run():
        agent = await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
            force_plan=True,   # force the arc regardless of the classifier
        )
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="add a comment to hello.py")]},
            config={"configurable": {"thread_id": "scripted-arc-1"}, "recursion_limit": 50},
        )

    result = asyncio.run(_run())

    # The scripted edit really landed on disk — execute phase actually ran.
    assert "# greeting helper" in (tmp_path / "hello.py").read_text(encoding="utf-8")
    # All 4 scripted turns were consumed: plan, execute(edit), EXECUTION
    # COMPLETE, VERIFIED OK — proving PEV walked plan -> execute -> verify.
    # (_pev_phase is a private LangGraph state channel, not in the public
    # ainvoke() return schema — the turn count is the observable proof that
    # every phase transition actually fired; a stall on any of them would
    # leave turns unconsumed at 1-3.)
    assert model.turn == 4, f"expected all 4 script turns consumed, model stopped at turn {model.turn}"


def test_scripted_model_plan_review_pauses_then_accepts(tmp_path, monkeypatch):
    """plan_review=True -> the SAME marker-emitting script pauses at the
    plan->execute boundary; resuming with accept lets execute/verify proceed."""
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    from hcode_v2.agent import factory

    (tmp_path / "hello.py").write_text(
        'def greet(name):\n    return "hello " + name\n', encoding="utf-8"
    )
    model = fresh_scripted_model(full_arc_script("hello.py"))
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: model)
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))

    async def _run():
        agent = await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
            plan_review=True,
        )
        cfg = {"configurable": {"thread_id": "scripted-pr-1"}, "recursion_limit": 50}
        # First invoke pauses at the interrupt — the file must NOT be touched yet.
        await agent.ainvoke(
            {"messages": [HumanMessage(content="add a comment to hello.py")]}, config=cfg,
        )
        state = await agent.aget_state(cfg)
        paused_before_edit = "# greeting helper" not in (tmp_path / "hello.py").read_text(encoding="utf-8")
        # Resume with accept -> execute really runs.
        await agent.ainvoke(Command(resume={"accept": True}), config=cfg)
        return paused_before_edit

    paused_before_edit = asyncio.run(_run())

    assert paused_before_edit, "plan-review did not pause before execute — the edit already landed"
    assert "# greeting helper" in (tmp_path / "hello.py").read_text(encoding="utf-8")
