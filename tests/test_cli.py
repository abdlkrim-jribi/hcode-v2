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


# --- mcp subcommands ------------------------------------------------------


# mcp is now a click.group; every subcommand must show up in its help and
# expose its own --help. The read-only subcommands (list/known/status) must run
# without touching the network.
_MCP_SUBCOMMANDS = ("list", "known", "connect", "add", "remove", "status")


def test_mcp_group_help_lists_all_subcommands() -> None:
    result = runner.invoke(cli, ["mcp", "--help"])
    assert result.exit_code == 0
    for sub in _MCP_SUBCOMMANDS:
        assert sub in result.output


def test_each_mcp_subcommand_has_help() -> None:
    for sub in _MCP_SUBCOMMANDS:
        result = runner.invoke(cli, ["mcp", sub, "--help"])
        assert result.exit_code == 0, f"mcp {sub} --help failed"
        assert "Usage" in result.output


def test_mcp_list_runs_without_config(tmp_path: Path, monkeypatch) -> None:
    # No config file in a fresh working dir -> friendly "no servers" message,
    # exit 0, and no network access.
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["mcp", "list"])
    assert result.exit_code == 0
    assert "No servers configured" in result.output


def test_mcp_status_runs_without_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["mcp", "status"])
    assert result.exit_code == 0
    assert "No servers configured" in result.output


def test_mcp_add_then_list_status_remove_roundtrip(tmp_path: Path, monkeypatch) -> None:
    # add a custom stdio server, confirm list/status see it, then remove it.
    monkeypatch.chdir(tmp_path)

    add = runner.invoke(
        cli,
        ["mcp", "add", "my-server", "--command", "npx",
         "--args", "-y", "--args", "some-pkg", "--env", "TOKEN=abc"],
    )
    assert add.exit_code == 0, add.output
    assert "my-server" in add.output

    listed = runner.invoke(cli, ["mcp", "list"])
    assert listed.exit_code == 0
    assert "my-server" in listed.output

    status = runner.invoke(cli, ["mcp", "status"])
    assert status.exit_code == 0
    assert "my-server" in status.output

    removed = runner.invoke(cli, ["mcp", "remove", "my-server"])
    assert removed.exit_code == 0
    assert "my-server" in removed.output

    # gone now -> back to the empty-config message.
    after = runner.invoke(cli, ["mcp", "list"])
    assert after.exit_code == 0
    assert "No servers configured" in after.output


def test_mcp_add_requires_exactly_one_transport(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # neither --command nor --url -> error, non-zero exit.
    result = runner.invoke(cli, ["mcp", "add", "bad-server"])
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()


def test_mcp_remove_unknown_server_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["mcp", "remove", "nope"])
    assert result.exit_code != 0


def test_mcp_connect_rejects_unknown_preset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["mcp", "connect", "definitely-not-a-preset"])
    assert result.exit_code == 0
    assert "Unknown server" in result.output
