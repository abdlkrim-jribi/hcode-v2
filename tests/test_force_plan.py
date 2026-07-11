"""Tests for force-plan mode — the composer's "Plan" toggle forcing the full arc.

ForcePlanMiddleware overrides PEV's TaskClassifier so a task the classifier would
route to "fast" instead runs the full plan→execute→verify arc. Mirrors the
plan-review tests: framework-faithful before_agent merge + factory attach/absence.
"""

from __future__ import annotations

import asyncio

from hcode_v2.agent.force_plan import ForcePlanMiddleware


def _run_before_agents_in_registration_order(task: str, middlewares) -> dict:
    """Run each middleware's before_agent in registration order, merging updates
    into state before the next — exactly how the agent framework chains them."""
    from langchain_core.messages import HumanMessage

    state: dict = {"messages": [HumanMessage(content=task)]}
    for mw in middlewares:
        update = mw.before_agent(state, None)
        if update:
            state.update(update)
    return state


def test_force_plan_overrides_classifier_for_simple_task():
    """A "simple" task the classifier routes to fast is pinned to plan when
    ForcePlanMiddleware runs after PEV (the registration order the factory uses)."""
    from deepagents.middleware.pev import PEVMiddleware
    from langchain_core.messages import HumanMessage

    task = "add a comment to hello.py"
    # Prove the premise: PEV alone routes this to "fast".
    pev_only = PEVMiddleware().before_agent({"messages": [HumanMessage(content=task)]}, None)
    assert pev_only["_pev_phase"] == "fast"

    # PEV then ForcePlan (factory order) → merged phase is "plan".
    state = _run_before_agents_in_registration_order(task, [PEVMiddleware(), ForcePlanMiddleware()])
    assert state["_pev_phase"] == "plan"


def test_force_plan_leaves_complex_task_on_plan():
    """A complex task already routes to plan; force-plan is a harmless no-op-value."""
    from deepagents.middleware.pev import PEVMiddleware

    state = _run_before_agents_in_registration_order(
        "implement a reverse function", [PEVMiddleware(), ForcePlanMiddleware()]
    )
    assert state["_pev_phase"] == "plan"


def test_force_plan_idempotent_when_another_hook_also_sets_plan():
    """Multiple before_agent hooks setting _pev_phase="plan" compose cleanly.

    Mirrors the plan_review + force_plan case (both write "plan"): the merged
    result is "plan" regardless of order, since the write is idempotent. Uses a
    second ForcePlanMiddleware as a stand-in for any co-registered plan-forcing
    hook so the test does not depend on C1's plan_review branch."""
    from deepagents.middleware.pev import PEVMiddleware

    state = _run_before_agents_in_registration_order(
        "add a comment to hello.py",
        [PEVMiddleware(), ForcePlanMiddleware(), ForcePlanMiddleware()],
    )
    assert state["_pev_phase"] == "plan"


# ── Factory: attached after PEV when on; absent when off (zero regression) ────

def _capture_factory_middleware(monkeypatch, tmp_path, **agent_kwargs) -> list:
    from hcode_v2.agent import factory

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
            **agent_kwargs,
        )

    asyncio.run(_build())
    return captured["middleware"]


def test_factory_registers_force_plan_after_pev(tmp_path, monkeypatch):
    from deepagents.middleware.pev import PEVMiddleware

    middleware = _capture_factory_middleware(monkeypatch, tmp_path, force_plan=True)
    types = [type(m) for m in middleware]
    assert ForcePlanMiddleware in types
    assert types.index(PEVMiddleware) < types.index(ForcePlanMiddleware)


def test_factory_omits_force_plan_by_default(tmp_path, monkeypatch):
    """force_plan absent → middleware absent → the classifier's verdict stands."""
    middleware = _capture_factory_middleware(monkeypatch, tmp_path)
    assert ForcePlanMiddleware not in [type(m) for m in middleware]
