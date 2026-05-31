"""Smoke tests for the HCode v2 CLI.

Every test here is network-free: it either inspects `--help` output or exercises
commands that only read the local filesystem (`skill`, `workflow`, `mcp known`,
`version`). The agent-invoking paths of `run`/`chat` are covered only at the
`--help` level so no model call is ever made.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from hcode_v2.cli.main import cli

runner = CliRunner()


def test_version_prints_hcode_v2() -> None:
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "HCode v2" in result.output


def test_top_level_help_lists_all_commands() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "chat", "mcp", "skill", "workflow", "version"):
        assert command in result.output


def test_each_command_has_help() -> None:
    for command in ("run", "chat", "mcp", "skill", "workflow", "version"):
        result = runner.invoke(cli, [command, "--help"])
        assert result.exit_code == 0, f"{command} --help failed"
        assert "Usage" in result.output


def test_run_and_chat_expose_workdir_flag() -> None:
    for command in ("run", "chat"):
        result = runner.invoke(cli, [command, "--help"])
        assert "--workdir" in result.output
        assert "-C" in result.output


def test_run_rejects_a_nonexistent_workdir() -> None:
    result = runner.invoke(cli, ["run", "noop", "--workdir", "this/dir/does/not/exist"])
    # Click raises BadParameter -> exit code 2, and never reaches the model.
    assert result.exit_code == 2
    assert "workdir" in result.output.lower()


def test_skill_reports_empty_directory(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["skill", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No skills" in result.output


def test_skill_lists_a_present_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# my-skill\n", encoding="utf-8")
    result = runner.invoke(cli, ["skill", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "my-skill" in result.output


def test_workflow_reports_empty_directory(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["workflow", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No workflows" in result.output


def test_workflow_lists_a_present_workflow(tmp_path: Path) -> None:
    (tmp_path / "ship-it.md").write_text("# ship-it\n", encoding="utf-8")
    result = runner.invoke(cli, ["workflow", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "ship-it" in result.output


def test_mcp_known_lists_preset_servers() -> None:
    result = runner.invoke(cli, ["mcp", "known"])
    assert result.exit_code == 0
    # The known-servers catalog renders a Rich table with these column headers.
    assert "Name" in result.output
    assert "Description" in result.output
