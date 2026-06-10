"""Smoke tests for the HCode v2 CLI.

Every test here is network-free: it either inspects `--help` output or exercises
commands that only read the local filesystem (`skill`, `workflow`, `mcp known`,
`version`). The agent-invoking paths of `run`/`chat` are covered only at the
`--help` level so no model call is ever made.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import hcode_v2.cli.main as cli_main
from hcode_v2.cli.main import cli

runner = CliRunner()


def test_version_prints_hcode_v2() -> None:
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "HCode v2" in result.output


def test_top_level_help_lists_all_commands() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "chat", "mcp", "skill", "workflow", "init", "config", "analyze", "explore", "version"):
        assert command in result.output


def test_each_command_has_help() -> None:
    for command in ("run", "chat", "mcp", "skill", "workflow", "init", "config", "analyze", "explore", "version"):
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


# --- analyze / explore ----------------------------------------------------
#
# analyze/explore are thin wrappers over the same agent run path as `run`; they
# only pre-shape the task prompt. These tests assert the prompt builders and the
# command wiring WITHOUT a live model by patching the shared _run_agent_task to
# capture the (task, workdir) it would have run — so no network call is made.


def test_analyze_and_explore_appear_in_help() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "analyze" in result.output
    assert "explore" in result.output


def test_analyze_and_explore_have_help() -> None:
    for command in ("analyze", "explore"):
        result = runner.invoke(cli, [command, "--help"])
        assert result.exit_code == 0, f"{command} --help failed"
        assert "Usage" in result.output


def test_build_analyze_task_default_and_deep() -> None:
    base = cli_main._build_analyze_task("src", deep=False)
    assert "Analyze the code at src." in base
    assert "structure" in base and "risks" in base and "improvements" in base

    deep = cli_main._build_analyze_task("src", deep=True)
    # --deep extends the base prompt rather than replacing it.
    assert deep.startswith(base)
    assert len(deep) > len(base)


def test_build_explore_task_embeds_query_and_asks_for_refs() -> None:
    task = cli_main._build_explore_task("where is auth handled?")
    assert "Explore this codebase to answer: where is auth handled?." in task
    assert "file references" in task


def _patch_capture(monkeypatch) -> dict:
    """Replace the shared run path with a no-network capture and return the store."""
    captured: dict = {}

    def fake_run(task, workdir, **kwargs):
        captured["task"] = task
        captured["workdir"] = workdir
        captured["kwargs"] = kwargs

    monkeypatch.setattr(cli_main, "_run_agent_task", fake_run)
    return captured


def test_analyze_runs_built_prompt_through_run_path(tmp_path: Path, monkeypatch) -> None:
    captured = _patch_capture(monkeypatch)
    result = runner.invoke(cli, ["analyze", "src", "-C", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["task"] == cli_main._build_analyze_task("src", deep=False)
    assert captured["workdir"] == str(tmp_path)


def test_analyze_defaults_path_to_dot(monkeypatch) -> None:
    captured = _patch_capture(monkeypatch)
    result = runner.invoke(cli, ["analyze"])
    assert result.exit_code == 0
    assert captured["task"] == cli_main._build_analyze_task(".", deep=False)


def test_analyze_deep_flag_changes_prompt(monkeypatch) -> None:
    captured = _patch_capture(monkeypatch)
    result = runner.invoke(cli, ["analyze", "src", "--deep"])
    assert result.exit_code == 0
    assert captured["task"] == cli_main._build_analyze_task("src", deep=True)


def test_explore_runs_built_prompt_through_run_path(monkeypatch) -> None:
    captured = _patch_capture(monkeypatch)
    result = runner.invoke(cli, ["explore", "how does login work?"])
    assert result.exit_code == 0
    assert captured["task"] == cli_main._build_explore_task("how does login work?")


# --- banner / welcome / status line ---------------------------------------
#
# Presentation-only helpers. Rendered to recorded text (no live terminal, no
# network) and asserted on the plain output.


def test_render_banner_contains_hcode() -> None:
    from rich.console import Console

    from hcode_v2.cli.banner import render_banner

    console = Console(record=True, width=100)
    console.print(render_banner())
    assert "HCODE" in console.export_text()


def test_render_welcome_renders_tips() -> None:
    from rich.console import Console

    from hcode_v2.cli.banner import render_welcome

    console = Console(record=True, width=100)
    console.print(render_welcome())
    out = console.export_text()
    assert "/exit" in out


def test_bare_hcode_shows_banner_and_help() -> None:
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "HCODE" in result.output
    # ...followed by the normal command help.
    assert "Commands" in result.output


def test_status_line_includes_model(monkeypatch) -> None:
    monkeypatch.setenv("HCODE_MODEL_NAME", "status-test-model")
    from rich.console import Console

    from hcode_v2.cli.statusline import render_status

    console = Console(record=True, width=120)
    console.print(render_status(mode="PEV"))
    assert "status-test-model" in console.export_text()


def test_version_is_exactly_the_one_liner() -> None:
    # The banner wiring must not leak into `version`: it stays the plain string.
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == cli_main._VERSION
