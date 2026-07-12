"""Tests for prompt slimming — phase-aware trimming of the assembled prompt.

The text fixtures embed the EXACT vendored section headers/snippets measured in
the M1 slimming audit (they are stable — the vendored tree is pinned, Rule 14).
"""

from __future__ import annotations

import asyncio

import pytest

from hcode_v2.agent.prompt_slim import (
    PromptSlimMiddleware,
    slim_excluded_tools,
    slim_level,
    slim_system_text,
)

# A miniature but structurally-faithful assembled system prompt: base text,
# dead vendored sections, an alive vendored section, skills, harness note, PEV.
ASSEMBLED = """You are a deep agent, an AI assistant.

## `write_todos`

You have access to the `write_todos` tool to help you manage complex objectives.

## Important To-Do List Usage Notes to Remember
- The `write_todos` tool should never be called multiple times in parallel.

## Following Conventions

- Read files before editing — understand existing content before making changes

## Filesystem Tools `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`

All file paths must start with a /.

## Large Tool Results

Use `read_file` to inspect the saved result in chunks.

## Execute Tool `execute`

You have access to an `execute` tool.

## `task` (subagent spawner)

You have access to a `task` tool to launch short-lived subagents.

## Important Task Tool Usage Notes to Remember

- Remember to use the `task` tool to silo independent tasks.

## HCode Skills

### concise-planning
Plan in atomic, verb-first steps.

### clean-code
Readable, small, intention-named code.

## Names

Use intention-revealing names (this internal header MUST NOT end the skills
region — the real clean-code skill contains exactly this structure).

## Functions

Small functions, one job.

### run-tests-before-done
Run tests for real before claiming EXECUTION COMPLETE.

### code-review
Structured Python review.

## Path & Tool Convention (authoritative — overrides any earlier note)

Ignore any instruction above claiming file paths must start with `/`.

## PEV Planning Phase

You are in the PLANNING phase.
PLAN COMPLETE"""


def test_dead_sections_excised_alive_kept_all_phases():
    for phase in ("plan", "execute", "verify", "fast"):
        out = slim_system_text(ASSEMBLED, phase)
        for dead in ("## `write_todos`", "## Filesystem Tools", "## Large Tool Results",
                     "## Execute Tool `execute`", "## `task` (subagent spawner)",
                     "## Important Task Tool Usage Notes", "## Important To-Do List"):
            assert dead not in out, f"{dead} survived in phase {phase}"
        # load-bearing text survives
        assert "## Following Conventions" in out
        assert "## Path & Tool Convention" in out          # harness note
        assert "## PEV Planning Phase" in out              # PEV phase prompt
        assert "You are a deep agent" in out               # base prompt


def test_plan_keeps_only_planning_skill():
    out = slim_system_text(ASSEMBLED, "plan")
    assert "### concise-planning" in out
    assert "### clean-code" not in out
    assert "### run-tests-before-done" not in out
    assert "### code-review" not in out
    # clean-code's INTERNAL "## Names"/"## Functions" headers go with it — the
    # region boundary must not stop at a skill-internal header (the bug the
    # first measurement caught).
    assert "## Names" not in out
    assert "## Functions" not in out


def test_verify_drops_the_whole_skills_section():
    out = slim_system_text(ASSEMBLED, "verify")
    assert "## HCode Skills" not in out
    assert "### concise-planning" not in out


def test_execute_keeps_working_set_drops_planning_and_review():
    for phase in ("execute", "fast"):
        out = slim_system_text(ASSEMBLED, phase)
        assert "### clean-code" in out
        assert "### run-tests-before-done" in out
        assert "### concise-planning" not in out
        assert "### code-review" not in out


def test_missing_anchors_noop():
    plain = "Just a prompt with ## Some Other Section\nand text."
    assert slim_system_text(plain, "execute") == plain


def test_excluded_tools_by_level():
    assert slim_excluded_tools("0") == frozenset()
    assert slim_excluded_tools("1") == frozenset({"task"})
    mx = slim_excluded_tools("max")
    assert "task" in mx and "web_search" in mx and "notebook_edit" in mx
    assert len(mx) == 7


def test_slim_level_resolution(monkeypatch):
    monkeypatch.delenv("HCODE_SLIM_PROMPT", raising=False)
    assert slim_level() == "1"                      # default ON at the safe level
    for raw, want in [("0", "0"), ("off", "0"), ("false", "0"),
                      ("1", "1"), ("weird", "1"), ("max", "max"), ("MAX", "max")]:
        monkeypatch.setenv("HCODE_SLIM_PROMPT", raw)
        assert slim_level() == want, raw


# ── middleware behaviour through wrap_model_call ─────────────────────────────

class _Req:
    """Minimal ModelRequest stand-in: state, system_message, model_settings,
    override() with the same semantics (returns a copy with replacements)."""
    def __init__(self, text, phase, settings=None):
        from langchain_core.messages import SystemMessage
        self.state = {"_pev_phase": phase}
        self.system_message = SystemMessage(content=text)
        self.model_settings = settings or {}
    def override(self, **kw):
        import copy
        new = copy.copy(self)
        for k, v in kw.items():
            setattr(new, k, v)
        return new


def _run(mw, req):
    seen = {}
    async def handler(r):
        seen["req"] = r
        return "resp"
    asyncio.run(mw.awrap_model_call(req, handler))
    return seen["req"]


def test_middleware_slims_text_and_caps_only_at_max():
    req = _Req(ASSEMBLED, "plan")
    out1 = _run(PromptSlimMiddleware(level="1"), req)
    assert "## `task` (subagent spawner)" not in out1.system_message.content
    assert "max_tokens" not in (out1.model_settings or {})   # level 1: no cap

    outm = _run(PromptSlimMiddleware(level="max"), _Req(ASSEMBLED, "plan"))
    assert outm.model_settings.get("max_tokens") == 2048      # plan cap
    oute = _run(PromptSlimMiddleware(level="max"), _Req(ASSEMBLED, "execute"))
    assert oute.model_settings.get("max_tokens") == 1280      # execute cap
    outv = _run(PromptSlimMiddleware(level="max"), _Req(ASSEMBLED, "verify"))
    assert outv.model_settings.get("max_tokens") == 768       # verify cap


def test_middleware_never_breaks_on_weird_requests():
    req = _Req(ASSEMBLED, "plan")
    req.system_message = None                                  # no system msg
    out = _run(PromptSlimMiddleware(level="1"), req)
    assert out.system_message is None                          # graceful no-op


# ── factory wiring ────────────────────────────────────────────────────────────

def _capture_factory(monkeypatch, tmp_path, env_level):
    from hcode_v2.agent import factory
    captured = {}
    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs); return object()
    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: object())
    # create_hcode_agent writes os.environ["HCODE_ROOT_DIR"] (factory.py) —
    # register it with monkeypatch so the write is UNDONE after this test and
    # doesn't leak a dead tmp path into later tests (test_rich_feed caught this).
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    if env_level is None:
        monkeypatch.delenv("HCODE_SLIM_PROMPT", raising=False)
    else:
        monkeypatch.setenv("HCODE_SLIM_PROMPT", env_level)
    (tmp_path / "skills").mkdir(exist_ok=True)
    async def _build():
        await factory.create_hcode_agent(
            persist=False, mcp_config=str(tmp_path / "none.json"),
            skills_dir=str(tmp_path / "skills"), workflows_dir=str(tmp_path),
            work_dir=str(tmp_path),
        )
    asyncio.run(_build())
    return captured["middleware"]


def test_factory_appends_slim_by_default_and_last(monkeypatch, tmp_path):
    mws = _capture_factory(monkeypatch, tmp_path, None)
    names = [type(m).__name__ for m in mws]
    assert names[-1] == "PromptSlimMiddleware"


def test_factory_omits_slim_at_level_0(monkeypatch, tmp_path):
    mws = _capture_factory(monkeypatch, tmp_path, "0")
    assert "PromptSlimMiddleware" not in [type(m).__name__ for m in mws]
