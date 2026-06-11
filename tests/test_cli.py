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


# --- chat input completion --------------------------------------------------
#
# Pure unit tests against the completers: build a prompt_toolkit Document and
# collect the offered completions. No TTY, no event loop, no network.


from prompt_toolkit.document import Document  # noqa: E402

from hcode_v2.cli.completion import (  # noqa: E402
    CHAT_COMMANDS,
    PHRASES,
    FilePathCompleter,
    PhraseCompleter,
    SlashCommandCompleter,
    build_chat_completer,
    build_chat_session,
)


def _completions(completer, text: str) -> list[str]:
    """Collect completion texts for `text` with the cursor at the end."""
    document = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(document, None)]


def test_chat_commands_catalog_matches_wired_dispatch() -> None:
    # The completer must offer exactly what the chat loop dispatches — no
    # dead entries (e.g. /help comes only when it is wired in C4).
    assert set(CHAT_COMMANDS) == {"/exit", "/quit", "/skills", "/workflows"}


def test_slash_completer_offers_skills_for_sk_prefix() -> None:
    assert _completions(SlashCommandCompleter(), "/sk") == ["/skills"]


def test_slash_completer_offers_all_commands_on_bare_slash() -> None:
    assert set(_completions(SlashCommandCompleter(), "/")) == set(CHAT_COMMANDS)


def test_slash_completer_silent_after_first_word_or_without_slash() -> None:
    completer = SlashCommandCompleter()
    assert _completions(completer, "/skills now") == []
    assert _completions(completer, "hello") == []


def test_file_completer_offers_matching_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    completer = FilePathCompleter(root_dir=str(tmp_path))
    assert _completions(completer, "look at src/") == ["src/app.py"]
    assert _completions(completer, "src/ap") == ["src/app.py"]


def test_file_completer_handles_at_references(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    completer = FilePathCompleter(root_dir=str(tmp_path))
    assert _completions(completer, "summarize @REA") == ["@README.md"]


def test_file_completer_skips_ignored_dirs_and_plain_words(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("x\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("x\n", encoding="utf-8")
    completer = FilePathCompleter(root_dir=str(tmp_path))
    # ignored directory contents are never offered
    assert _completions(completer, "node_modules/") == []
    # a bare word (no separator, no @) is not treated as a path
    assert _completions(completer, "keep") == []


def test_phrase_completer_offers_curated_phrase() -> None:
    offered = _completions(PhraseCompleter(), "fix")
    assert "fix the bug in" in offered
    assert all(phrase in PHRASES for phrase in offered)


def test_phrase_completer_silent_on_commands_and_short_input() -> None:
    completer = PhraseCompleter()
    assert _completions(completer, "/fix") == []
    assert _completions(completer, "f") == []


def test_merged_completer_routes_each_kind(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x\n", encoding="utf-8")
    completer = build_chat_completer(work_dir=str(tmp_path))
    assert "/skills" in _completions(completer, "/sk")
    assert "fix the bug in" in _completions(completer, "fix")
    assert "@main.py" in _completions(completer, "@ma")


def test_build_chat_session_creates_history_dir(tmp_path: Path) -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    history = tmp_path / ".hcode" / "chat_history.txt"
    # Pipe input + dummy output so no real terminal is required.
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            session = build_chat_session(
                work_dir=str(tmp_path), history_path=str(history)
            )
    assert history.parent.is_dir()
    assert session.auto_suggest is not None
    assert session.completer is not None


# --- chat slash-command dispatch --------------------------------------------
#
# Confirms the four wired commands still dispatch exactly as before with the
# prompt_toolkit input layer in place. The agent factory and the prompt
# session are both replaced with fakes: no network, no TTY.


class _FakePromptSession:
    """Replays scripted inputs through prompt_async, then EOF."""

    def __init__(self, inputs: list[str]) -> None:
        self._inputs = list(inputs)

    async def prompt_async(self, _message: str) -> str:
        if not self._inputs:
            raise EOFError
        return self._inputs.pop(0)


class _FakeAgent:
    """Records astream_events calls; replays a scripted raw event stream."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.calls: list[dict] = []
        self.configs: list[dict | None] = []
        self._events = events or []

    async def astream_events(self, payload: dict, config: dict | None = None, version: str = "v2"):
        self.calls.append(payload)
        self.configs.append(config)
        for event in self._events:
            yield event


def _run_chat_with_inputs(monkeypatch, inputs: list[str], events: list[dict] | None = None) -> tuple:
    agent = _FakeAgent(events)

    async def fake_create_agent(**_kwargs):
        return agent

    monkeypatch.setattr(cli_main, "create_hcode_agent", fake_create_agent)
    monkeypatch.setattr(
        cli_main, "build_chat_session", lambda **_kwargs: _FakePromptSession(inputs)
    )
    result = runner.invoke(cli, ["chat"])
    return result, agent


def test_chat_exit_and_quit_end_session_without_agent_call(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for command in ("/exit", "/quit"):
        result, agent = _run_chat_with_inputs(monkeypatch, [command])
        assert result.exit_code == 0, result.output
        assert "Goodbye." in result.output
        assert agent.calls == []


def test_chat_skills_and_workflows_dispatch_locally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".hcode" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# my-skill\n", encoding="utf-8")
    workflow_dir = tmp_path / ".hcode" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ship-it.md").write_text("# ship-it\n", encoding="utf-8")

    result, agent = _run_chat_with_inputs(monkeypatch, ["/skills", "/workflows", "/exit"])
    assert result.exit_code == 0, result.output
    assert "my-skill" in result.output
    assert "ship-it" in result.output
    # Local commands never reach the agent.
    assert agent.calls == []


def test_chat_plain_message_goes_to_agent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result, agent = _run_chat_with_inputs(monkeypatch, ["hello there", "/exit"])
    assert result.exit_code == 0, result.output
    assert len(agent.calls) == 1
    assert agent.calls[0]["messages"][0].content == "hello there"


def test_chat_stream_config_sets_explicit_recursion_limit(tmp_path: Path, monkeypatch) -> None:
    # The astream_events path silently drops the agent's bound recursion_limit
    # (langchain_core stamps its default 25 into the config), so chat must pass
    # one explicitly or long tasks die at 25 supersteps.
    monkeypatch.chdir(tmp_path)
    result, agent = _run_chat_with_inputs(monkeypatch, ["do something", "/exit"])
    assert result.exit_code == 0, result.output
    assert len(agent.configs) == 1
    config = agent.configs[0]
    assert config is not None
    assert config.get("recursion_limit", 25) > 25
    # thread_id wiring is unchanged
    assert "thread_id" in config["configurable"]


def test_chat_shows_final_text_from_stream(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    events = [
        {"event": "on_chat_model_end",
         "data": {"output": SimpleNamespace(content="hi back")}},
    ]
    result, agent = _run_chat_with_inputs(monkeypatch, ["hello", "/exit"], events=events)
    assert result.exit_code == 0, result.output
    assert "hi back" in result.output


# --- live turn renderer ------------------------------------------------------
#
# Unit tests for LiveTurnRenderer: feed synthetic raw astream_events dicts, no
# real agent, no TTY (recording console), no network. The live-region content
# is asserted via renderable() since non-terminal consoles render Live lazily.


from types import SimpleNamespace  # noqa: E402

from rich.console import Console as RichConsole  # noqa: E402

from hcode_v2.cli.live import LiveTurnRenderer  # noqa: E402


def _token_event(text: str) -> dict:
    return {
        "event": "on_chat_model_stream",
        "data": {"chunk": SimpleNamespace(content=text)},
    }


def _render(renderer: LiveTurnRenderer, console: RichConsole) -> str:
    console.print(renderer.renderable())
    return console.export_text()


def test_live_renders_plan_panel_on_marker() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_token_event("1. Add the endpoint. 2. Test it. "))
        renderer.process_event(_token_event("PLAN COMPLETE"))
    out = console.export_text()
    assert "Plan" in out
    assert "Add the endpoint" in out


def test_live_renders_plan_only_once() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_token_event("the plan PLAN COMPLETE"))
        renderer.process_event(_token_event("more text PLAN COMPLETE again"))
    assert console.export_text().count("Plan ") == 1 or console.export_text().count("─ Plan ─") == 1


def test_live_checklist_from_hcode_todo_write() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    renderer.process_event({
        "event": "on_tool_start",
        "name": "todo_write",
        "data": {"input": {"todos": [
            {"text": "write tests", "done": False},
            {"text": "fix bug", "done": True},
        ]}},
    })
    out = _render(renderer, console)
    assert "[ ] write tests" in out
    assert "[x] fix bug" in out


def test_live_checklist_from_deepagents_write_todos() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    renderer.process_event({
        "event": "on_tool_start",
        "name": "write_todos",
        "data": {"input": {"todos": [
            {"content": "scaffold module", "status": "completed"},
            {"content": "wire it up", "status": "in_progress"},
            {"content": "add tests", "status": "pending"},
        ]}},
    })
    out = _render(renderer, console)
    assert "[x] scaffold module" in out
    assert "[>] wire it up" in out
    assert "[ ] add tests" in out


def test_live_shows_and_clears_running_tool() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    renderer.process_event({"event": "on_tool_start", "name": "read_file", "data": {"input": {}}})
    assert "running read_file" in _render(renderer, console)
    renderer.process_event({"event": "on_tool_end", "name": "read_file", "data": {}})
    fresh = RichConsole(record=True, width=100)
    assert "running read_file" not in _render(renderer, fresh)


def test_live_captures_final_text() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    renderer.process_event({
        "event": "on_chat_model_end",
        "data": {"output": SimpleNamespace(content="All done.")},
    })
    assert renderer.final_text == "All done."


def _model_end(text: str) -> dict:
    return {
        "event": "on_chat_model_end",
        "data": {"output": SimpleNamespace(content=text)},
    }


def test_live_final_answer_prefers_execute_text_over_verify_verdict() -> None:
    # PEV turn: the LAST model output is the verify verdict (often echoing the
    # plan). The user-facing answer must be the execute-phase result, with the
    # verdict surfaced as a short status note instead.
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_token_event("1. create calc.py 2. test it\nPLAN COMPLETE"))
        renderer.process_event(_model_end("1. create calc.py 2. test it\nPLAN COMPLETE"))
        renderer.process_event(_model_end(
            "Created calc.py and test_calc.py; the test passes.\nEXECUTION COMPLETE"
        ))
        renderer.process_event(_model_end(
            "1. create calc.py 2. test it — all steps done.\nVERIFIED OK"
        ))
    assert "Created calc.py" in renderer.final_text
    assert "VERIFIED OK" not in renderer.final_text
    assert "EXECUTION COMPLETE" not in renderer.final_text
    assert renderer.verify_status == "verified"
    # the verdict shows up as a short status note in the scrollback
    assert "verified" in console.export_text().lower()


def test_live_verdict_only_run_falls_back_to_stripped_verdict() -> None:
    # No execute-phase text at all (e.g. tool-only execute turns): fall back to
    # the verdict text, but without the raw marker.
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_model_end("Everything matches the plan.\nVERIFIED OK"))
    assert renderer.final_text == "Everything matches the plan."
    assert renderer.verify_status == "verified"


def test_live_issues_found_sets_status_not_answer() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_model_end("Fixing the export now.\nDone."))
        renderer.process_event(_model_end("ISSUES FOUND: test still failing"))
    assert renderer.verify_status == "issues found"
    assert "ISSUES FOUND" not in renderer.final_text
    assert "Fixing the export" in renderer.final_text


def test_live_full_synthetic_turn() -> None:
    # plan + todo_write + tool + done, end to end through the context manager.
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_token_event("Plan: do the thing. PLAN COMPLETE"))
        renderer.process_event({
            "event": "on_tool_start",
            "name": "todo_write",
            "data": {"input": {"todos": [{"text": "do the thing", "done": False}]}},
        })
        renderer.process_event({"event": "on_tool_end", "name": "todo_write", "data": {}})
        renderer.process_event(_token_event("doing it... EXECUTION COMPLETE"))
        renderer.process_event({
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(content="Done: the thing.")},
        })
    out = console.export_text() + _render(renderer, console)
    assert "do the thing" in out
    assert renderer.final_text == "Done: the thing."


def test_live_trivial_turn_renders_nothing_extra() -> None:
    # fast path: no plan marker, no todos — no panel, no checklist, no error.
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    with renderer:
        renderer.process_event(_token_event("just a quick answer"))
        renderer.process_event({
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(content="quick answer")},
        })
    out = console.export_text()
    assert "Plan" not in out
    assert "[x]" not in out and "[ ]" not in out
    assert renderer.final_text == "quick answer"


def test_live_never_raises_on_malformed_events() -> None:
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    malformed = [
        {},
        {"event": "on_chat_model_stream"},
        {"event": "on_chat_model_stream", "data": {}},
        {"event": "on_chat_model_stream", "data": {"chunk": None}},
        {"event": "on_tool_start"},
        {"event": "on_tool_start", "name": "todo_write", "data": {"input": "garbage"}},
        {"event": "on_tool_start", "name": "todo_write", "data": {"input": {"todos": "garbage"}}},
        {"event": "on_tool_start", "name": "write_todos", "data": {"input": {"todos": [{"status": "pending"}]}}},
        {"event": "on_chat_model_end", "data": {}},
        {"event": "on_chat_model_end", "data": {"output": None}},
        {"event": "something_unknown", "data": {"x": 1}},
    ]
    with renderer:
        for event in malformed:
            renderer.process_event(event)
    assert renderer.final_text == ""
