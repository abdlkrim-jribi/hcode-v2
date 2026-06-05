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
    for command in ("run", "chat", "mcp", "skill", "workflow", "init", "config", "version"):
        assert command in result.output


def test_each_command_has_help() -> None:
    for command in ("run", "chat", "mcp", "skill", "workflow", "init", "config", "version"):
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


# --- init -----------------------------------------------------------------


def test_init_scaffolds_an_empty_dir(tmp_path: Path) -> None:
    result = runner.invoke(cli, ["init", "-C", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".env").is_file()
    for sub in (".hcode/skills", ".hcode/workflows", ".hcode/sessions"):
        assert (tmp_path / sub).is_dir(), f"missing {sub}"
    assert (tmp_path / ".hcode" / "mcp_config.json").is_file()


def test_init_is_idempotent_and_preserves_env(tmp_path: Path) -> None:
    # An existing .env must not be clobbered on a plain re-init.
    (tmp_path / ".env").write_text("HCODE_MODEL_NAME=custom\n", encoding="utf-8")
    result = runner.invoke(cli, ["init", "-C", str(tmp_path)])
    assert result.exit_code == 0
    assert "exists" in result.output
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "HCODE_MODEL_NAME=custom\n"


def test_init_force_overwrites_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("STALE=1\n", encoding="utf-8")
    result = runner.invoke(cli, ["init", "-C", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "STALE" not in (tmp_path / ".env").read_text(encoding="utf-8")


# --- config ---------------------------------------------------------------


def test_config_subcommands_have_help() -> None:
    for sub in ("list", "get", "set"):
        result = runner.invoke(cli, ["config", sub, "--help"])
        assert result.exit_code == 0, f"config {sub} --help failed"
        assert "Usage" in result.output


def test_config_set_then_get_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    set_res = runner.invoke(cli, ["config", "set", "HCODE_MODEL_NAME", "gpt-4o-test"])
    assert set_res.exit_code == 0, set_res.output
    # Persisted to the project-local .env, preserving the KEY=value form.
    assert (tmp_path / ".env").read_text(encoding="utf-8").strip() == "HCODE_MODEL_NAME=gpt-4o-test"
    get_res = runner.invoke(cli, ["config", "get", "HCODE_MODEL_NAME"])
    assert get_res.exit_code == 0
    assert "gpt-4o-test" in get_res.output


def test_config_set_rejects_unknown_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["config", "set", "NOT_A_KEY", "x"])
    assert result.exit_code != 0
    assert "Unknown config key" in result.output


def test_config_set_rejects_bad_toolcall_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["config", "set", "HCODE_TOOLCALL_MODE", "bogus"])
    assert result.exit_code != 0


def test_config_set_rejects_non_integer_max_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["config", "set", "HCODE_MAX_TOKENS", "lots"])
    assert result.exit_code != 0


def test_config_get_masks_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-supersecretvalue123")
    result = runner.invoke(cli, ["config", "get", "HCODE_MODEL_API_KEY"])
    assert result.exit_code == 0
    assert "supersecretvalue" not in result.output
    assert "****" in result.output


def test_config_list_masks_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-supersecretvalue123")
    result = runner.invoke(cli, ["config", "list"])
    assert result.exit_code == 0
    assert "supersecretvalue" not in result.output
    assert "****" in result.output
