"""Unit tests for SelectiveSkillsMiddleware and the factory's active_skills param.

All tests are network-free: no model is built, no graph is compiled.
The factory's ``create_deep_agent`` and ``_build_model`` are replaced with fakes
(same pattern as ``test_factory_tool_routing.py`` and ``test_daemon_session_cache.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hcode_v2.agent.selective_skills import SelectiveSkillsMiddleware
from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware
from hcode_v2.agent import factory


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_skill_dir(root: Path, name: str) -> None:
    """Create a minimal skill directory under root/."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {name}\nDescription for {name}.\n")


def _capture_middleware(monkeypatch, tmp_path: Path, **factory_kwargs) -> list:
    """Run create_hcode_agent with faked internals; return the middleware list."""
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    # Accept any args (the model_override kwarg from GUI model selection) so this
    # stub stays valid as _build_model's signature grows additively.
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: object())
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))

    (tmp_path / "workflows").mkdir(exist_ok=True)

    async def _build() -> None:
        await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_such_mcp.json"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
            **factory_kwargs,
        )

    asyncio.run(_build())
    return captured.get("middleware", [])


# ── SelectiveSkillsMiddleware unit tests ──────────────────────────────────────

class TestSelectiveSkillsMiddleware:
    def test_loads_only_allowed_skills(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "skill-a")
        _make_skill_dir(tmp_path, "skill-b")
        _make_skill_dir(tmp_path, "skill-c")

        mw = SelectiveSkillsMiddleware(
            skills_dir=str(tmp_path), allow=frozenset({"skill-a", "skill-c"})
        )
        skills = mw._load_skills_from_dir()
        names = {s["name"] for s in skills}
        assert names == {"skill-a", "skill-c"}

    def test_excludes_skills_not_in_allow(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "keep")
        _make_skill_dir(tmp_path, "drop")

        mw = SelectiveSkillsMiddleware(
            skills_dir=str(tmp_path), allow=frozenset({"keep"})
        )
        skills = mw._load_skills_from_dir()
        assert [s["name"] for s in skills] == ["keep"]

    def test_empty_allow_returns_no_skills(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "skill-a")

        mw = SelectiveSkillsMiddleware(
            skills_dir=str(tmp_path), allow=frozenset()
        )
        assert mw._load_skills_from_dir() == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        mw = SelectiveSkillsMiddleware(
            skills_dir=str(tmp_path / "nonexistent"), allow=frozenset({"x"})
        )
        assert mw._load_skills_from_dir() == []

    def test_is_subclass_of_vendored_middleware(self) -> None:
        assert issubclass(SelectiveSkillsMiddleware, HCodeSkillsMiddleware)

    def test_skill_dir_placeholder_resolved(self, tmp_path: Path) -> None:
        """The {skill_dir} placeholder in SKILL.md must still expand to the real path."""
        d = tmp_path / "my-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("See {skill_dir}/helper.py for details.")

        mw = SelectiveSkillsMiddleware(
            skills_dir=str(tmp_path), allow=frozenset({"my-skill"})
        )
        skills = mw._load_skills_from_dir()
        assert len(skills) == 1
        assert str(d) in skills[0]["content"]
        assert "{skill_dir}" not in skills[0]["content"]


# ── Factory integration: active_skills param ─────────────────────────────────

class TestFactoryActiveSkills:
    def test_none_uses_plain_middleware(self, monkeypatch, tmp_path: Path) -> None:
        """active_skills=None → the vanilla HCodeSkillsMiddleware (all skills)."""
        mw_list = _capture_middleware(monkeypatch, tmp_path, active_skills=None)
        skills_mws = [m for m in mw_list if isinstance(m, HCodeSkillsMiddleware)]
        assert len(skills_mws) == 1
        assert type(skills_mws[0]) is HCodeSkillsMiddleware  # plain, not subclass

    def test_empty_list_uses_plain_middleware(self, monkeypatch, tmp_path: Path) -> None:
        """active_skills=[] → treat as None (footgun guard), plain middleware."""
        mw_list = _capture_middleware(monkeypatch, tmp_path, active_skills=[])
        skills_mws = [m for m in mw_list if isinstance(m, HCodeSkillsMiddleware)]
        assert len(skills_mws) == 1
        assert type(skills_mws[0]) is HCodeSkillsMiddleware

    def test_list_uses_selective_middleware(self, monkeypatch, tmp_path: Path) -> None:
        """active_skills=['clean-code'] → SelectiveSkillsMiddleware with that allowlist."""
        mw_list = _capture_middleware(
            monkeypatch, tmp_path, active_skills=["clean-code", "tdd-lite"]
        )
        selective = [m for m in mw_list if isinstance(m, SelectiveSkillsMiddleware)]
        assert len(selective) == 1
        assert selective[0]._allow == frozenset({"clean-code", "tdd-lite"})

    def test_selective_only_loads_chosen_skills(self, monkeypatch, tmp_path: Path) -> None:
        """End-to-end: only the named skills appear in what the middleware loads."""
        from hcode_v2.skills_path import builtin_skills_dir
        skills_root = builtin_skills_dir()
        if not skills_root.is_dir():
            pytest.skip("built-in skills dir not present")

        mw_list = _capture_middleware(
            monkeypatch, tmp_path, active_skills=["clean-code"]
        )
        selective = next(m for m in mw_list if isinstance(m, SelectiveSkillsMiddleware))
        loaded = selective._load_skills_from_dir()
        assert [s["name"] for s in loaded] == ["clean-code"]

    def test_all_builtins_load_with_none(self, monkeypatch, tmp_path: Path) -> None:
        """active_skills=None → every built-in skill directory loads (unchanged behavior).

        Asserts against the ACTUAL directory listing, not a hardcoded count — a
        hardcoded number here previously broke the moment a skill was added (or a
        stray untracked directory appeared in a local checkout) without the two
        ever being reconciled. Counting the real dirs makes this self-updating.
        """
        from hcode_v2.skills_path import builtin_skills_dir
        skills_root = builtin_skills_dir()
        if not skills_root.is_dir():
            pytest.skip("built-in skills dir not present")

        expected_names = {
            d.name for d in skills_root.iterdir()
            if d.is_dir() and (d / "SKILL.md").is_file()
        }

        mw_list = _capture_middleware(monkeypatch, tmp_path, active_skills=None)
        plain = next(m for m in mw_list if type(m) is HCodeSkillsMiddleware)
        loaded = plain._load_skills_from_dir()
        assert {s["name"] for s in loaded} == expected_names
