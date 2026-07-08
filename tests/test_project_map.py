"""Tests for project-map injection (audit #1 — plan without sight).

Covers the builder (build_project_map) against synthetic tmp projects and the
factory integration (_build_context_prompt): a real folder yields a bounded
<project_map> naming REAL files; an empty/unreadable dir omits it entirely
(zero regression); size stays under the hard cap even for a huge repo.
"""

from __future__ import annotations

import json
from pathlib import Path

from hcode_v2.agent.project_map import _MAX_CHARS, build_project_map


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_python_project(root: Path) -> None:
    """A small src-layout Python project with manifest, README, tests, junk."""
    (root / "pyproject.toml").write_text(
        '[project]\n'
        'name = "acme-widgets"\n'
        'description = "Widget maker"\n'
        'dependencies = ["click>=8.0", "rich", "httpx>=0.27"]\n'
        '[project.scripts]\n'
        'acme = "acme.cli:main"\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Acme Widgets\n\nMakes widgets.\n", encoding="utf-8")
    pkg = root / "src" / "acme"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text("def main(): ...\n", encoding="utf-8")
    (pkg / "widgets.py").write_text("class Widget: ...\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_widgets.py").write_text("def test_it(): ...\n", encoding="utf-8")
    # Junk that must NOT appear:
    (root / "node_modules").mkdir()
    (root / "node_modules" / "left-pad.js").write_text("//\n", encoding="utf-8")
    venv = root / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "junk.py").write_text("x\n", encoding="utf-8")
    (root / "uv.lock").write_text("lock\n" * 200, encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n")


# ── VERIFY #1: map contains tree + manifest + README ──────────────────────────

def test_map_contains_manifest_tree_readme(tmp_path: Path) -> None:
    _make_python_project(tmp_path)
    m = build_project_map(str(tmp_path))

    assert m.startswith("<project_map>") and m.endswith("</project_map>")
    # Manifest summary
    assert "acme-widgets" in m
    assert "Widget maker" in m
    assert "click" in m and "rich" in m and "httpx" in m
    assert "acme = acme.cli:main" in m  # entry point
    # Tree
    assert "Structure:" in m
    # README
    assert "Acme Widgets" in m and "Makes widgets" in m


# ── VERIFY #2: the map NAMES REAL FILES (so the plan phase can reference them) ─

def test_map_names_real_files_not_fabricated(tmp_path: Path) -> None:
    _make_python_project(tmp_path)
    m = build_project_map(str(tmp_path))

    # Real source files appear by name (src-layout compression reaches them).
    assert "cli.py" in m
    assert "widgets.py" in m
    assert "test_widgets.py" in m
    # Single-child chain compressed: src/ holds only acme/ → shown together.
    assert "src/acme/" in m


def test_map_omits_junk_dirs_and_binaries(tmp_path: Path) -> None:
    _make_python_project(tmp_path)
    m = build_project_map(str(tmp_path))

    assert "node_modules" not in m
    assert ".venv" not in m and "junk.py" not in m
    assert "uv.lock" not in m       # lockfile filtered
    assert "logo.png" not in m      # binary filtered


# ── VERIFY: gitignore-awareness (best-effort dir names) ───────────────────────

def test_map_honors_gitignore_directory_names(tmp_path: Path) -> None:
    _make_python_project(tmp_path)
    secret = tmp_path / "private_data"
    secret.mkdir()
    (secret / "secrets.py").write_text("KEY=1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("private_data/\n*.log\n!keep.me\n", encoding="utf-8")

    m = build_project_map(str(tmp_path))
    assert "private_data" not in m
    assert "secrets.py" not in m


# ── VERIFY #3: size bounded — a huge repo is summarized within the cap ─────────

def test_huge_repo_stays_under_char_cap(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "big"\n', encoding="utf-8")
    # 40 top-level dirs, each with 60 files and a nested subdir of 60 more.
    for i in range(40):
        d = tmp_path / f"pkg_{i:02d}"
        sub = d / "inner"
        sub.mkdir(parents=True)
        for j in range(60):
            (d / f"mod_{j:02d}.py").write_text("x\n", encoding="utf-8")
            (sub / f"deep_{j:02d}.py").write_text("y\n", encoding="utf-8")

    m = build_project_map(str(tmp_path))
    assert len(m) <= _MAX_CHARS, f"map is {len(m)} chars, over the {_MAX_CHARS} cap"
    # It must SUMMARIZE, not dump — overflow markers present, not all 4800 files.
    assert m.count("mod_") < 400
    assert "more" in m or "collapsed" in m or "files)" in m


def test_deep_dir_collapses_to_file_count(tmp_path: Path) -> None:
    # BRANCHING at each level (so single-child compression does NOT apply) plus
    # depth ≥ 3 → the depth-3 dir collapses to a "(N files)" count, not expanded.
    #   top/ (2 dirs) → branch_a/ (2 dirs) → sub1/ (depth 3 → count)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    a1 = tmp_path / "top" / "branch_a" / "sub1"
    a1.mkdir(parents=True)
    (tmp_path / "top" / "branch_a" / "sub2").mkdir()          # 2nd dir → no compress
    (tmp_path / "top" / "branch_b").mkdir()                   # 2nd top dir → no compress
    (tmp_path / "top" / "branch_b" / "z.py").write_text("z\n", encoding="utf-8")
    for j in range(12):
        (a1 / f"f{j}.py").write_text("x\n", encoding="utf-8")

    m = build_project_map(str(tmp_path))
    # sub1/ sits at the depth limit → shown as a "(12 files)" count, not expanded.
    assert "(12 files)" in m
    assert "f0.py" not in m  # too deep to enumerate


# ── VERIFY #4: empty / unreadable work_dir → omitted gracefully ───────────────

def test_empty_dir_yields_empty_map(tmp_path: Path) -> None:
    assert build_project_map(str(tmp_path)) == ""


def test_nonexistent_dir_yields_empty_map(tmp_path: Path) -> None:
    assert build_project_map(str(tmp_path / "does_not_exist")) == ""


def test_file_instead_of_dir_yields_empty_map(tmp_path: Path) -> None:
    f = tmp_path / "afile.txt"
    f.write_text("hi\n", encoding="utf-8")
    assert build_project_map(str(f)) == ""


def test_project_with_only_junk_yields_empty_map(tmp_path: Path) -> None:
    # A dir containing ONLY skipped content produces no map (no manifest, no
    # visible tree, no README) → "" → the agent keeps its bare env block.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("//\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    assert build_project_map(str(tmp_path)) == ""


# ── Manifest variety ──────────────────────────────────────────────────────────

def test_package_json_manifest(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({
            "name": "my-app", "description": "A web app",
            "dependencies": {"react": "^18", "vite": "^5"},
            "scripts": {"dev": "vite", "build": "vite build"},
        }),
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text("<html></html>\n", encoding="utf-8")
    m = build_project_map(str(tmp_path))
    assert "my-app" in m and "Node/JS" in m
    assert "react" in m and "vite" in m
    assert "A web app" in m


def test_requirements_txt_manifest(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "flask==2.0\nrequests>=2.0\n# a comment\ngunicorn\n", encoding="utf-8"
    )
    (tmp_path / "app.py").write_text("app = 1\n", encoding="utf-8")
    m = build_project_map(str(tmp_path))
    assert "flask" in m and "requests" in m and "gunicorn" in m
    assert "# a comment" not in m


def test_malformed_manifest_does_not_crash(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("this is [ not valid toml =====\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    m = build_project_map(str(tmp_path))
    # No header (bad manifest), but the tree still renders — never raises.
    assert "main.py" in m


# ── Factory integration: _build_context_prompt ───────────────────────────────

def test_context_prompt_appends_map_for_real_project(tmp_path: Path) -> None:
    from hcode_v2.agent.factory import _build_context_prompt, _build_env_block

    _make_python_project(tmp_path)
    ctx = _build_context_prompt(str(tmp_path))
    env = _build_env_block(str(tmp_path))

    assert env in ctx                      # env facts still present, unchanged
    assert "<project_map>" in ctx          # map appended
    assert "widgets.py" in ctx             # real files reach the (all-phase) prompt


def test_context_prompt_is_bare_env_for_empty_dir(tmp_path: Path) -> None:
    # Zero regression: no map → identical to the pre-existing env-only behaviour.
    from hcode_v2.agent.factory import _build_context_prompt, _build_env_block

    ctx = _build_context_prompt(str(tmp_path))
    assert ctx == _build_env_block(str(tmp_path))
    assert "<project_map>" not in ctx


def test_real_agent_build_passes_map_as_system_prompt(tmp_path: Path, monkeypatch) -> None:
    """create_hcode_agent hands create_deep_agent a system_prompt containing the
    <project_map> with real files — so it rides the USER segment into EVERY phase.
    PEV's plan prompt is APPENDED to (not replacing) that system message, so the
    PLAN phase sees the map. Same kwargs-capture harness as test_factory_tool_routing.
    """
    import asyncio

    from hcode_v2.agent import factory

    _make_python_project(tmp_path)
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: object())
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    (tmp_path / "workflows").mkdir(exist_ok=True)

    async def _build() -> None:
        await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_such_mcp.json"),
            skills_dir=str(tmp_path / "skills"),  # absent → no skills, fine
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
        )

    asyncio.run(_build())

    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "<project_map>" in system_prompt
    assert "widgets.py" in system_prompt and "cli.py" in system_prompt
    # env facts still lead the segment (USER prompt is env + map, in that order).
    assert system_prompt.index("<env>") < system_prompt.index("<project_map>")
