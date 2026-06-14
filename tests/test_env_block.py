"""Tests for ``_build_env_block`` in ``hcode_v2.agent.factory``.

``_build_env_block(work_dir: str) -> str`` does not exist yet — these tests
pin the contract for the OpenCode-style ``<env>`` context block that will be
prepended to the agent system prompt: a small, declarative set of facts
(working directory, platform, git-repo flag, date) and explicitly NOT a file
listing.
"""

from __future__ import annotations

import os
import platform
from datetime import date
from pathlib import Path

from hcode_v2.agent.factory import _build_env_block


def _make_block(work_dir: Path) -> str:
    return _build_env_block(str(work_dir))


# 1. Wrapped in <env> ... </env>
def test_block_is_wrapped_in_env_tags(tmp_path: Path) -> None:
    block = _make_block(tmp_path).strip()
    assert block.startswith("<env>")
    assert block.endswith("</env>")


# 2. Contains the resolved absolute working directory
def test_block_contains_absolute_work_dir(tmp_path: Path) -> None:
    block = _make_block(tmp_path)
    assert os.path.abspath(str(tmp_path)) in block


# 3. "Platform:" followed by platform.system()
def test_block_contains_platform(tmp_path: Path) -> None:
    block = _make_block(tmp_path)
    assert f"Platform: {platform.system()}" in block


# 4a. Is git repo: yes  — when a .git/ directory exists in work_dir
def test_block_reports_git_repo_yes_when_dotgit_present(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    block = _make_block(tmp_path)
    assert "Is git repo: yes" in block


# 4b. Is git repo: no  — when no .git/ is present
def test_block_reports_git_repo_no_when_dotgit_absent(tmp_path: Path) -> None:
    block = _make_block(tmp_path)
    assert "Is git repo: no" in block


# 5. Contains today's date in ISO format
def test_block_contains_today_iso_date(tmp_path: Path) -> None:
    block = _make_block(tmp_path)
    assert date.today().isoformat() in block


# 6. Does NOT contain a file listing / tree of work_dir
def test_block_has_no_file_listing(tmp_path: Path) -> None:
    sentinel = "DO_NOT_LIST_ME.marker"
    (tmp_path / sentinel).write_text("x", encoding="utf-8")
    block = _make_block(tmp_path)
    assert sentinel not in block
