"""HCode v2 CLI — entry point for the ``hcode`` and ``hcode_v2`` commands."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# Propagate into os.environ so subprocesses inherit them
_OPENAI_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_URL = os.getenv("OPENAI_BASE_URL")
if _OPENAI_KEY:
    os.environ["OPENAI_API_KEY"] = _OPENAI_KEY
if _OPENAI_URL:
    os.environ["OPENAI_BASE_URL"] = _OPENAI_URL

import click
from langchain_core.messages import HumanMessage

from hcode_v2.agent.factory import create_hcode_agent
from hcode_v2.cli.display import HCodeDisplay

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


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """HCode v2 — autonomous AI coding agent."""


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
    display = HCodeDisplay()
    _validate_workdir(workdir)

    if fast:
        task = "/fast " + task

    display.show_task_header(task)
    display.console.print(f"[dim]Working directory: {workdir or os.getcwd()}[/dim]")

    async def _invoke() -> str:
        import datetime

        agent = await create_hcode_agent(enable_pev=not no_pev, work_dir=workdir)
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
    model_name = os.getenv("HCODE_MODEL", "gpt-4o-mini")
    if session:
        click.echo(f"Resuming session: {session_id}")
    else:
        click.echo(f"New session: {session_id}")
    display.console.print(f"[bold blue]HCode v2 Chat[/bold blue]  (model: {model_name})")
    display.console.print(f"[dim]Working directory: {workdir or os.getcwd()}[/dim]")
    display.console.print("[dim]Type /exit or /quit to end the session.[/dim]\n")

    async def _chat_loop() -> None:
        agent = await create_hcode_agent(session_id=session_id, work_dir=workdir)

        while True:
            try:
                user_input = input("you> ").strip()
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
                result = await agent.ainvoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config={"configurable": {"thread_id": session_id}},
                )
                result_messages = result.get("messages", [])
                response_text = (
                    _extract_text(result_messages[-1].content)
                    if result_messages
                    else "(no response)"
                )
                display.show_result(response_text)
            except Exception as exc:  # noqa: BLE001
                display.show_error(str(exc))

    asyncio.run(_chat_loop())


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------


@cli.command(name="mcp")
@click.argument("action", type=click.Choice(["list", "connect", "known"]))
@click.argument("server", required=False)
def mcp_cmd(action: str, server: str | None) -> None:
    """Manage MCP server connections.

    Actions:
      list     Show configured servers from .hcode/mcp_config.json
      known    Show all available preset servers
      connect  Add a preset server to your config
    """
    from deepagents.mcp.client import MCPClientManager

    display = HCodeDisplay()
    manager = MCPClientManager()

    if action == "known":
        servers = manager.list_known_servers()
        from rich.table import Table
        table = Table(title="Available MCP Servers")
        table.add_column("Name", style="cyan")
        table.add_column("Description")
        table.add_column("Requires", style="yellow")
        for s in servers:
            requires = ", ".join(s.get("env_required", [])) or "-"
            table.add_row(s["name"], s["description"], requires)
        display.console.print(table)
        return

    if action == "list":
        if not manager.is_configured:
            display.console.print(
                "[dim]No servers configured. "
                "Run: hcode mcp known[/dim]"
            )
            return
        try:
            config = json.loads(
                Path(".hcode/mcp_config.json").read_text()
            )
            servers_dict = config.get("servers", {})
            from rich.table import Table
            table = Table(title="Configured MCP Servers")
            table.add_column("Name", style="cyan")
            table.add_column("Command")
            table.add_column("Args")
            for name, cfg in servers_dict.items():
                args = " ".join(cfg.get("args", []))
                table.add_row(name, cfg.get("command", ""), args)
            display.console.print(table)
        except Exception as e:
            display.show_error(str(e))
        return

    if action == "connect":
        if not server:
            display.console.print(
                "[red]Usage: hcode mcp connect <server-name>[/red]"
            )
            display.console.print(
                "Run [cyan]hcode mcp known[/cyan] to see options"
            )
            return
        known = manager.get_known_server(server)
        if not known:
            display.show_error(
                f"Unknown server: '{server}'. "
                f"Run 'hcode mcp known' to see available servers."
            )
            return
        if manager.is_configured:
            try:
                existing = json.loads(
                    Path(".hcode/mcp_config.json").read_text()
                )
                if server in existing.get("servers", {}):
                    display.console.print(
                        f"[yellow]{server} already configured.[/yellow]"
                    )
                    return
            except Exception:
                pass
        manager.add_server_to_config(server, known)
        display.console.print(
            f"[green]Added '{server}' to .hcode/mcp_config.json[/green]"
        )
        if known.get("env_required"):
            display.console.print(
                f"[yellow]Required env vars: "
                f"{', '.join(known['env_required'])}[/yellow]"
            )
            display.console.print(
                "[dim]Add them to your .env file.[/dim]"
            )


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
