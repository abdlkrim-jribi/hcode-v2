"""HCode v2 CLI — entry point for the ``hcode`` and ``hcode_v2`` commands."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from langchain_core.messages import HumanMessage

from hcode_v2.agent.factory import create_hcode_agent
from hcode_v2.cli.display import HCodeDisplay

_VERSION = "HCode v2.0.0 — powered by DeepAgents + LangGraph"
_DEFAULT_MODEL = "openai:gpt-4o"


def _extract_text(content: object) -> str:
    """Pull plain text out of an AIMessage content value.

    Args:
        content: Either a plain string or a list of content-block dicts.

    Returns:
        The concatenated text content.
    """
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
@click.option("--model", "-m", default=_DEFAULT_MODEL, show_default=True, help="LangChain model string.")
@click.option("--no-pev", is_flag=True, default=False, help="Disable Plan-Execute-Verify loop.")
@click.option("--fast", is_flag=True, default=False, help="Skip planning — execute in one shot.")
def run(task: str, model: str, no_pev: bool, fast: bool) -> None:
    """Run a single TASK and print the result."""
    display = HCodeDisplay()

    if fast:
        task = "/fast " + task

    display.show_task_header(task)

    async def _invoke() -> str:
        agent = await create_hcode_agent(model=model, enable_pev=not no_pev)
        result = await agent.ainvoke({"messages": [HumanMessage(content=task)]})
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
@click.option("--model", "-m", default=_DEFAULT_MODEL, show_default=True, help="LangChain model string.")
def chat(model: str) -> None:
    """Start an interactive chat session with the HCode agent.

    Special commands:
      /skills    — list available skills
      /workflows — list available workflows
      /exit, /quit — end the session
    """
    display = HCodeDisplay()
    display.console.print(f"[bold blue]HCode v2 Chat[/bold blue]  (model: {model})")
    display.console.print("[dim]Type /exit or /quit to end the session.[/dim]\n")

    async def _chat_loop() -> None:
        agent = await create_hcode_agent(model=model)
        messages: list = []

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

            messages.append(HumanMessage(content=user_input))
            try:
                result = await agent.ainvoke({"messages": messages})
                messages = result.get("messages", messages)
                response_text = _extract_text(messages[-1].content) if messages else "(no response)"
                display.show_result(response_text)
            except Exception as exc:  # noqa: BLE001
                display.show_error(str(exc))

    asyncio.run(_chat_loop())


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------


@cli.group()
def mcp() -> None:
    """Manage MCP server connections."""


@mcp.command("list")
@click.option("--config", default=".hcode/mcp_config.json", show_default=True, help="MCP config file path.")
def mcp_list(config: str) -> None:
    """List MCP servers defined in the config file."""
    display = HCodeDisplay()
    config_path = Path(config)

    if not config_path.exists():
        display.console.print(f"[dim]Config file not found: {config}[/dim]")
        return

    data: dict = json.loads(config_path.read_text())
    servers = [
        {"name": server_id, "command": srv.get("command", ""), "status": "configured"}
        for server_id, srv in data.get("servers", {}).items()
    ]
    display.show_mcp_servers(servers)


@mcp.command("connect")
@click.argument("server")
@click.option("--config", default=".hcode/mcp_config.json", show_default=True, help="MCP config file path.")
def mcp_connect(server: str, config: str) -> None:
    """Connect to SERVER and list its available tools."""
    display = HCodeDisplay()
    from deepagents.mcp.client import MCPClient, MCPClientManager

    async def _connect() -> None:
        manager = MCPClientManager(config_path=config)
        await manager.connect_all()
        client = manager.get_client(server)
        if client is None:
            display.show_error(f"Server '{server}' not found or failed to connect.")
            return
        tools = client.tools
        if not tools:
            display.console.print(f"[dim]No tools reported by server '{server}'.[/dim]")
            return
        display.console.print(f"\n[bold cyan]Tools from '{server}':[/bold cyan]")
        for tool in tools:
            display.console.print(f"  [bold]{tool.name}[/bold]: {tool.description}")
        await manager.disconnect_all()

    try:
        asyncio.run(_connect())
    except Exception as exc:  # noqa: BLE001
        display.show_error(str(exc))
        sys.exit(1)


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
