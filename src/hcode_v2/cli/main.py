"""HCode v2 CLI — entry point for the ``hcode`` and ``hcode_v2`` commands."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
# Project-local .env (cwd) wins; the repo-root .env stays as a fallback so existing
# setups — including the daemon — keep working unchanged. load_dotenv does not
# override already-set vars, so loading cwd first gives it precedence.
load_dotenv(Path.cwd() / ".env")
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# Propagate into os.environ so subprocesses inherit them
_OPENAI_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_URL = os.getenv("OPENAI_BASE_URL")
if _OPENAI_KEY:
    os.environ["OPENAI_API_KEY"] = _OPENAI_KEY
if _OPENAI_URL:
    os.environ["OPENAI_BASE_URL"] = _OPENAI_URL

# Force Python child processes (pytest etc.) to emit UTF-8 even when their
# stdout is a pipe — otherwise on Windows they write the ANSI code page
# (cp1252/cp1256) and our UTF-8 readers hit invalid bytes. Tool subprocesses
# (terminal.py, base.py, diff.py) inherit os.environ, so one mutation here
# covers them all. setdefault: respect an explicit user override.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import click
from langchain_core.messages import HumanMessage

from hcode_v2.agent.factory import create_hcode_agent
from hcode_v2.cli.banner import render_banner, render_welcome
from hcode_v2.cli.completion import build_chat_session
from hcode_v2.cli.live import LiveTurnRenderer
from hcode_v2.cli.display import HCodeDisplay
from hcode_v2.cli.statusline import render_status
from hcode_v2.utils.config import Config

_VERSION = "HCode v2.0.0 — powered by DeepAgents + LangGraph"


def _extract_text(content: object) -> str:
    """Pull plain text out of an AIMessage content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _validate_workdir(workdir: str | None) -> None:
    """Reject a ``--workdir`` that does not point at an existing directory.

    Validates before the agent is constructed so a bad path fails fast with a
    Click ``BadParameter`` (exit code 2) and never reaches the model. The path
    itself is passed through to ``create_hcode_agent(work_dir=...)`` — we do not
    ``os.chdir`` here.

    Args:
        workdir: Directory the user asked for, or ``None`` to use the default.

    Raises:
        click.BadParameter: If ``workdir`` is given but is not a directory.
    """
    if workdir is not None and not os.path.isdir(workdir):
        raise click.BadParameter(
            f"workdir does not exist: {workdir}", param_hint="'--workdir'"
        )


def _run_agent_task(task: str, workdir: str | None, *, enable_pev: bool = True) -> None:
    """Run a single pre-built TASK through the agent and display the result.

    This is the shared single-shot run path used by ``run``, ``analyze``, and
    ``explore``: build the agent with ``create_hcode_agent(work_dir=...)``,
    ``ainvoke`` it with one ``HumanMessage``, and render the final message text.
    ``analyze``/``explore`` differ from ``run`` only in how they pre-shape
    ``task`` — the invocation here is identical.

    Args:
        task: Fully-formed task prompt to send to the agent.
        workdir: Working directory for file operations, or ``None`` for cwd.
        enable_pev: Whether to enable the Plan-Execute-Verify loop.
    """
    display = HCodeDisplay()
    display.show_task_header(task)
    display.console.print(f"[dim]Working directory: {workdir or os.getcwd()}[/dim]")

    async def _invoke() -> str:
        import datetime

        agent = await create_hcode_agent(enable_pev=enable_pev, work_dir=workdir)
        thread_id = "run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        messages = result.get("messages", [])
        if not messages:
            return "(no response)"
        return _extract_text(messages[-1].content)

    try:
        text = asyncio.run(_invoke())
        display.show_result(text)
    except Exception as exc:  # noqa: BLE001
        display.show_error(str(exc))
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """HCode v2 — autonomous AI coding agent."""
    # Bare `hcode` (no subcommand): greet with the banner + welcome, then the
    # normal command help. Subcommands (incl. `version`) run untouched.
    if ctx.invoked_subcommand is None:
        display = HCodeDisplay()
        display.console.print(render_banner())
        display.console.print(render_welcome())
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("task")
@click.option("--no-pev", is_flag=True, default=False, help="Disable Plan-Execute-Verify loop.")
@click.option("--fast", is_flag=True, default=False, help="Skip planning — execute in one shot.")
@click.option("--workdir", "-w", "-C", default=None,
              help="Working directory for file operations. Defaults to current directory.")
def run(task: str, no_pev: bool, fast: bool, workdir: str | None) -> None:
    """Run a single TASK and print the result."""
    _validate_workdir(workdir)

    if fast:
        task = "/fast " + task

    _run_agent_task(task, workdir, enable_pev=not no_pev)


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--session", "-s", default=None, help="Session ID to resume. Defaults to new timestamped session.")
@click.option("--workdir", "-w", "-C", default=None,
              help="Working directory for file operations. Defaults to current directory.")
def chat(session: str | None, workdir: str | None) -> None:
    """Start an interactive chat session with the HCode agent.

    Special commands:
      /skills    — list available skills
      /workflows — list available workflows
      /exit, /quit — end the session
    """
    import datetime

    _validate_workdir(workdir)
    session_id = session or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    display = HCodeDisplay()

    # Banner once at session start, then session/working-dir context.
    display.console.print(render_banner())
    display.console.print(render_welcome())
    if session:
        click.echo(f"Resuming session: {session_id}")
    else:
        click.echo(f"New session: {session_id}")
    display.console.print(f"[dim]Working directory: {workdir or os.getcwd()}[/dim]")
    display.console.print("[dim]Type /exit or /quit to end the session.[/dim]\n")

    async def _chat_loop() -> None:
        agent = await create_hcode_agent(session_id=session_id, work_dir=workdir)
        # Input layer: completion (slash commands, file paths, phrases),
        # FileHistory + auto-suggest. Dispatch below is unchanged.
        prompt_session = build_chat_session(work_dir=workdir)

        while True:
            # Persistent-feel status line: re-rendered just above each prompt.
            # chat always runs the PEV loop; context% is not yet exposed here.
            display.console.print(render_status(mode="PEV", workdir=workdir))
            try:
                user_input = (await prompt_session.prompt_async("you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                display.console.print("\n[dim]Session ended.[/dim]")
                break

            if not user_input:
                continue

            if user_input in ("/exit", "/quit"):
                display.console.print("[dim]Goodbye.[/dim]")
                break

            if user_input == "/skills":
                skills = [
                    d.name
                    for d in Path(".hcode/skills").iterdir()
                    if d.is_dir() and (d / "SKILL.md").exists()
                ] if Path(".hcode/skills").is_dir() else []
                display.show_skills(skills)
                continue

            if user_input == "/workflows":
                workflows = [
                    p.stem
                    for p in Path(".hcode/workflows").glob("*.md")
                ] if Path(".hcode/workflows").is_dir() else []
                display.show_workflows(workflows)
                continue

            try:
                # C3: stream the turn so the plan and todo progress render
                # live. Display-only — same invocation semantics as ainvoke.
                renderer = LiveTurnRenderer(console=display.console)
                with renderer:
                    async for event in agent.astream_events(
                        {"messages": [HumanMessage(content=user_input)]},
                        # recursion_limit MUST be explicit on the astream_events
                        # path: langchain_core stamps its default (25) into the
                        # config, which overrides the agent's bound 9999 and
                        # kills tasks after ~5 tool rounds. The daemon's
                        # astream_events (server.py:198) has the same latent
                        # issue — fixed separately.
                        config={
                            "configurable": {"thread_id": session_id},
                            "recursion_limit": 1000,
                        },
                        version="v2",
                    ):
                        renderer.process_event(event)
                display.show_result(renderer.final_text or "(no response)")
            except Exception as exc:  # noqa: BLE001
                display.show_error(str(exc))

    asyncio.run(_chat_loop())


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------


@cli.group(name="mcp")
def mcp_cmd() -> None:
    """Manage MCP server connections.

    Subcommands:
      list     Show configured servers from .hcode/mcp_config.json
      known    Show all available preset servers
      connect  Add a preset server to your config
      add      Add a custom server (stdio --command or remote --url)
      remove   Delete a configured server
      status   Summarise the local MCP configuration (no network)
    """


@mcp_cmd.command(name="known")
def mcp_known() -> None:
    """Show all available preset MCP servers."""
    from deepagents.mcp.client import MCPClientManager
    from rich.table import Table

    display = HCodeDisplay()
    manager = MCPClientManager()
    servers = manager.list_known_servers()
    table = Table(title="Available MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Requires", style="yellow")
    for s in servers:
        requires = ", ".join(s.get("env_required", [])) or "-"
        table.add_row(s["name"], s["description"], requires)
    display.console.print(table)


@mcp_cmd.command(name="list")
def mcp_list() -> None:
    """Show configured servers from .hcode/mcp_config.json."""
    from deepagents.mcp.client import MCPClientManager
    from rich.table import Table

    display = HCodeDisplay()
    manager = MCPClientManager()

    if not manager.is_configured:
        display.console.print(
            "[dim]No servers configured. "
            "Run: hcode mcp known[/dim]"
        )
        return
    try:
        config = json.loads(manager.config_path.read_text())
        servers_dict = config.get("servers", {})
        table = Table(title="Configured MCP Servers")
        table.add_column("Name", style="cyan")
        table.add_column("Command")
        table.add_column("Args")
        for name, cfg in servers_dict.items():
            args = " ".join(cfg.get("args", []))
            command = cfg.get("command") or cfg.get("url", "")
            table.add_row(name, command, args)
        display.console.print(table)
    except Exception as e:
        display.show_error(str(e))


@mcp_cmd.command(name="connect")
@click.argument("server")
def mcp_connect(server: str) -> None:
    """Add a preset SERVER to your config (see `hcode mcp known`)."""
    from deepagents.mcp.client import MCPClientManager

    display = HCodeDisplay()
    manager = MCPClientManager()

    known = manager.get_known_server(server)
    if not known:
        display.show_error(
            f"Unknown server: '{server}'. "
            f"Run 'hcode mcp known' to see available servers."
        )
        return
    if manager.is_configured:
        try:
            existing = json.loads(manager.config_path.read_text())
            if server in existing.get("servers", {}):
                display.console.print(
                    f"[yellow]{server} already configured.[/yellow]"
                )
                return
        except Exception:
            pass
    manager.add_server_to_config(server, known)
    display.console.print(
        f"[green]Added '{server}' to {manager.config_path}[/green]"
    )
    if known.get("env_required"):
        display.console.print(
            f"[yellow]Required env vars: "
            f"{', '.join(known['env_required'])}[/yellow]"
        )
        display.console.print(
            "[dim]Add them to your .env file.[/dim]"
        )


@mcp_cmd.command(name="add")
@click.argument("server_id")
@click.option("--url", default=None, help="URL of a remote (HTTP/SSE) MCP server.")
@click.option("--command", default=None, help="Executable that launches a stdio MCP server.")
@click.option("--args", "args_", multiple=True, help="Argument for the command (repeatable).")
@click.option("--env", "env_", multiple=True, metavar="KEY=VAL",
              help="Environment variable for the server (repeatable).")
@click.option("--name", default=None, help="Human-readable display name. Defaults to SERVER_ID.")
@click.option("--description", default=None, help="Short description of the server.")
def mcp_add(
    server_id: str,
    url: str | None,
    command: str | None,
    args_: tuple[str, ...],
    env_: tuple[str, ...],
    name: str | None,
    description: str | None,
) -> None:
    """Add a custom MCP server to .hcode/mcp_config.json.

    Provide exactly one transport: ``--command`` (stdio) or ``--url`` (remote).
    """
    from deepagents.mcp.client import MCPClientManager

    display = HCodeDisplay()

    if bool(url) == bool(command):
        display.show_error("Provide exactly one of --url or --command.")
        sys.exit(1)

    env: dict[str, str] = {}
    for pair in env_:
        if "=" not in pair:
            display.show_error(f"Invalid --env '{pair}'; expected KEY=VAL.")
            sys.exit(1)
        key, value = pair.split("=", 1)
        env[key] = value

    entry: dict[str, object] = {}
    if command:
        entry["command"] = command
        entry["args"] = list(args_)
    else:
        entry["url"] = url
    if env:
        entry["env"] = env
    entry["name"] = name or server_id
    if description:
        entry["description"] = description

    # The preset helper (`add_server_to_config`, used by `connect`) only persists
    # command/args/env, so custom servers carrying url/name/description are written
    # here via a read-modify-write against the same config file.
    manager = MCPClientManager()
    config_path = manager.config_path
    config_data: dict = {"servers": {}}
    if config_path.exists():
        try:
            config_data = json.loads(config_path.read_text())
        except Exception:
            config_data = {"servers": {}}
    config_data.setdefault("servers", {})
    config_data["servers"][server_id] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config_data, indent=2))
    display.console.print(f"[green]Added '{server_id}' to {config_path}[/green]")


@mcp_cmd.command(name="remove")
@click.argument("server_id")
def mcp_remove(server_id: str) -> None:
    """Remove SERVER_ID from .hcode/mcp_config.json."""
    from deepagents.mcp.client import MCPClientManager

    display = HCodeDisplay()
    manager = MCPClientManager()
    config_path = manager.config_path

    if not config_path.exists():
        display.show_error(f"No MCP config at {config_path}.")
        sys.exit(1)
    try:
        config_data = json.loads(config_path.read_text())
    except Exception as e:
        display.show_error(str(e))
        sys.exit(1)

    servers = config_data.get("servers", {})
    if server_id not in servers:
        display.show_error(f"Server '{server_id}' is not configured.")
        sys.exit(1)
    del servers[server_id]
    config_path.write_text(json.dumps(config_data, indent=2))
    display.console.print(f"[green]Removed '{server_id}' from {config_path}[/green]")


@mcp_cmd.command(name="status")
def mcp_status() -> None:
    """Summarise the local MCP configuration (no network connections)."""
    from deepagents.mcp.client import MCPClientManager

    display = HCodeDisplay()
    manager = MCPClientManager()

    display.console.print(f"[bold]MCP config:[/bold] {manager.config_path}")
    if not manager.is_configured:
        display.console.print(
            "[dim]No servers configured. Run: hcode mcp known[/dim]"
        )
        return
    try:
        config = json.loads(manager.config_path.read_text())
    except Exception as e:
        display.show_error(str(e))
        return
    servers = config.get("servers", {})
    display.console.print(f"[green]{len(servers)} server(s) configured:[/green]")
    for name, cfg in servers.items():
        transport = cfg.get("command") or cfg.get("url", "")
        display.console.print(f"  • {name} [dim]({transport})[/dim]")


# ---------------------------------------------------------------------------
# skill
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--dir", "skills_dir", default=".hcode/skills", show_default=True, help="Skills directory.")
def skill(skills_dir: str) -> None:
    """List available HCode skills."""
    display = HCodeDisplay()
    root = Path(skills_dir)
    skills = (
        [d.name for d in sorted(root.iterdir()) if d.is_dir() and (d / "SKILL.md").exists()]
        if root.is_dir()
        else []
    )
    display.show_skills(skills)


# ---------------------------------------------------------------------------
# workflow
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--dir", "workflows_dir", default=".hcode/workflows", show_default=True, help="Workflows directory.")
def workflow(workflows_dir: str) -> None:
    """List available HCode workflows."""
    display = HCodeDisplay()
    root = Path(workflows_dir)
    workflows = (
        [p.stem for p in sorted(root.glob("*.md"))]
        if root.is_dir()
        else []
    )
    display.show_workflows(workflows)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


_ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


@cli.command()
@click.option("--workdir", "-w", "-C", default=None,
              help="Directory to scaffold. Defaults to current directory.")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite an existing .env and .hcode/mcp_config.json.")
def init(workdir: str | None, force: bool) -> None:
    """Scaffold an HCode v2 project (.env and the .hcode/ workspace).

    Idempotent by default: an existing .env or mcp_config.json is left untouched.
    Pass --force to overwrite them.
    """
    _validate_workdir(workdir)
    display = HCodeDisplay()
    root = Path(workdir) if workdir else Path.cwd()

    created: list[str] = []
    skipped: list[str] = []

    # .env — copied from the repo's .env.example template (the config carrier).
    env_path = root / ".env"
    if env_path.exists() and not force:
        skipped.append(".env")
    else:
        template = _ENV_EXAMPLE.read_text(encoding="utf-8") if _ENV_EXAMPLE.exists() else ""
        env_path.write_text(template, encoding="utf-8")
        created.append(".env")

    # Workspace directories the agent reads relative to the working directory.
    for sub in (".hcode/skills", ".hcode/workflows", ".hcode/sessions"):
        directory = root / sub
        if directory.is_dir():
            skipped.append(sub + "/")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            created.append(sub + "/")

    # MCP config — seeded empty so `hcode mcp add/connect` has a file to edit.
    mcp_path = root / ".hcode" / "mcp_config.json"
    if mcp_path.exists() and not force:
        skipped.append(".hcode/mcp_config.json")
    else:
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_path.write_text(json.dumps({"servers": {}}, indent=2) + "\n", encoding="utf-8")
        created.append(".hcode/mcp_config.json")

    display.console.print(f"[bold]Initialised HCode project in[/bold] {root}")
    for item in created:
        display.console.print(f"  [green]created[/green] {item}")
    for item in skipped:
        display.console.print(f"  [dim]exists [/dim] {item}")
    if ".env" in created:
        display.console.print(
            "\n[yellow]Edit .env to set your model and API key, "
            "or run `hcode config set ...`.[/yellow]"
        )


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


# Whitelisted, user-editable model/provider keys. These are the variables the
# agent actually reads (Config.from_env + the two factory-only keys), persisted
# to the project-local .env that the CLI loads at startup.
_CONFIG_KEYS: tuple[str, ...] = (
    "HCODE_MODEL_NAME",
    "HCODE_MODEL_API_KEY",
    "HCODE_MODEL_BASE_URL",
    "HCODE_TOOLCALL_MODE",
    "HCODE_MAX_TOKENS",
    "ANTHROPIC_API_KEY",
)
_SECRET_CONFIG_KEYS: frozenset[str] = frozenset(
    {"HCODE_MODEL_API_KEY", "ANTHROPIC_API_KEY"}
)


def _mask_secret(value: str | None) -> str:
    """Render a secret for display — e.g. ``sk-...****`` — never the full value."""
    if not value:
        return "(not set)"
    if len(value) <= 7:
        return "****"
    return f"{value[:3]}...****"


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Write ``KEY=value`` into a .env file, preserving existing lines and comments.

    Updates the key in place if a non-comment assignment already exists, otherwise
    appends it. Creates the file (and parent directory) when missing.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0].strip() == key:
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@cli.group(name="config")
def config_cmd() -> None:
    """View and edit model/provider configuration in the project .env.

    Subcommands:
      list           Show resolved config (secrets masked)
      get <KEY>      Print one key's effective value
      set <KEY> VAL  Write a key to ./.env (takes effect next run)
    """


@config_cmd.command(name="list")
def config_list() -> None:
    """Show the resolved model/provider configuration (secrets masked)."""
    from rich.table import Table

    display = HCodeDisplay()
    cfg = Config.from_env()
    rows = [
        ("HCODE_MODEL_NAME", cfg.model),
        ("HCODE_MODEL_API_KEY", _mask_secret(cfg.api_key)),
        ("HCODE_MODEL_BASE_URL", cfg.base_url or "(not set)"),
        ("HCODE_TOOLCALL_MODE", cfg.toolcall_mode),
        ("HCODE_MAX_TOKENS", os.getenv("HCODE_MAX_TOKENS", "2000")),
        ("ANTHROPIC_API_KEY", _mask_secret(os.getenv("ANTHROPIC_API_KEY"))),
    ]
    table = Table(title="HCode Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    for key, value in rows:
        table.add_row(key, value)
    display.console.print(table)


@config_cmd.command(name="get")
@click.argument("key")
def config_get(key: str) -> None:
    """Print the effective value of a single config KEY (secrets masked)."""
    display = HCodeDisplay()
    if key not in _CONFIG_KEYS:
        display.show_error(
            f"Unknown config key: '{key}'. "
            f"Valid keys: {', '.join(_CONFIG_KEYS)}."
        )
        sys.exit(1)
    value = os.getenv(key)
    if key in _SECRET_CONFIG_KEYS:
        click.echo(_mask_secret(value))
    else:
        click.echo(value if value is not None else "(not set)")


@config_cmd.command(name="set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Write KEY=VALUE to the project-local ./.env (takes effect next run)."""
    display = HCodeDisplay()
    if key not in _CONFIG_KEYS:
        display.show_error(
            f"Unknown config key: '{key}'. "
            f"Valid keys: {', '.join(_CONFIG_KEYS)}."
        )
        sys.exit(1)
    if key == "HCODE_TOOLCALL_MODE" and value not in ("native", "json", "auto"):
        display.show_error("HCODE_TOOLCALL_MODE must be one of: native, json, auto.")
        sys.exit(1)
    if key == "HCODE_MAX_TOKENS":
        try:
            int(value)
        except ValueError:
            display.show_error("HCODE_MAX_TOKENS must be an integer.")
            sys.exit(1)

    env_path = Path(".env")
    _set_env_value(env_path, key, value)
    # Reflect into the running process so a follow-up `config get`/`list` is live.
    os.environ[key] = value

    shown = _mask_secret(value) if key in _SECRET_CONFIG_KEYS else value
    display.console.print(f"[green]Set {key}={shown} in {env_path}[/green]")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def _build_analyze_task(path: str, deep: bool) -> str:
    """Build the analyze prompt for PATH, optionally requesting a deeper review."""
    task = (
        f"Analyze the code at {path}. "
        "Report structure, key risks, and concrete improvements."
    )
    if deep:
        task += (
            " Go deeper: trace the key control and data flows, inspect edge cases "
            "and error handling, and cite specific files and line references."
        )
    return task


@cli.command()
@click.argument("path", default=".")
@click.option("--deep", is_flag=True, default=False,
              help="Request a deeper, more thorough review.")
@click.option("--workdir", "-w", "-C", default=None,
              help="Working directory for file operations. Defaults to current directory.")
def analyze(path: str, deep: bool, workdir: str | None) -> None:
    """Analyze the code at PATH (default '.') — structure, risks, improvements."""
    _validate_workdir(workdir)
    task = _build_analyze_task(path, deep)
    _run_agent_task(task, workdir)


# ---------------------------------------------------------------------------
# explore
# ---------------------------------------------------------------------------


def _build_explore_task(query: str) -> str:
    """Build the explore prompt that answers QUERY from the codebase."""
    return (
        f"Explore this codebase to answer: {query}. "
        "Search the relevant files and summarize findings with file references."
    )


@cli.command()
@click.argument("query")
@click.option("--workdir", "-w", "-C", default=None,
              help="Working directory for file operations. Defaults to current directory.")
def explore(query: str, workdir: str | None) -> None:
    """Explore the codebase to answer QUERY, with file references."""
    _validate_workdir(workdir)
    task = _build_explore_task(query)
    _run_agent_task(task, workdir)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print the HCode v2 version string."""
    click.echo(_VERSION)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
