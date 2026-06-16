"""Rich display helpers for the HCode v2 CLI."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_PHASE_COLORS: dict[str, str] = {
    "plan": "yellow",
    "execute": "blue",
    "verify": "green",
    "fast": "cyan",
    "trivial": "white",
}


class HCodeDisplay:
    """Centralised Rich display helper for the HCode v2 CLI.

    All output goes through a single :class:`~rich.console.Console` instance so
    the caller never has to import Rich directly.
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console: Console = console or Console(force_terminal=True)

    def show_task_header(self, task: str) -> None:
        """Print a bordered panel announcing the task being executed.

        Args:
            task: The user-supplied task string.
        """
        self.console.print(
            Panel(
                "[bold]Task:[/bold] " + task,
                title="HCode v2",
                border_style="blue",
                expand=False,
            )
        )

    def show_phase_transition(self, phase: str) -> None:
        """Print a one-line phase-change notification.

        Args:
            phase: PEV phase name (``"plan"``, ``"execute"``, ``"verify"``,
                ``"fast"``, or ``"trivial"``).
        """
        color = _PHASE_COLORS.get(phase, "white")
        label = phase.upper()
        self.console.print(f"[{color}]▶  Phase: {label}[/{color}]")

    def show_result(self, result: str) -> None:
        """Print the agent's final response in a green panel.

        Args:
            result: Response text to display.
        """
        self.console.print(
            Panel(
                result,
                title="Result",
                border_style="green",
                expand=True,
            )
        )

    def show_error(self, error: str) -> None:
        """Print an error message in a red panel.

        Args:
            error: Error description to display.
        """
        self.console.print(
            Panel(
                f"[bold red]{error}[/bold red]",
                title="Error",
                border_style="red",
                expand=False,
            )
        )

    def show_mcp_servers(self, servers: list[dict[str, str]]) -> None:
        """Print a Rich table listing configured MCP servers.

        Args:
            servers: List of dicts with keys ``"name"``, ``"command"``, and
                ``"status"``.
        """
        if not servers:
            self.console.print("[dim]No MCP servers configured.[/dim]")
            return

        table = Table(title="MCP Servers", box=box.ROUNDED, border_style="blue")
        table.add_column("Name", style="bold cyan", no_wrap=True)
        table.add_column("Command", style="white")
        table.add_column("Status", style="dim")

        for srv in servers:
            table.add_row(
                srv.get("name", ""),
                srv.get("command", ""),
                srv.get("status", "configured"),
            )

        self.console.print(table)

    def show_skills(self, skills: list[str]) -> None:
        """Print a Rich table listing available HCode skills.

        Args:
            skills: List of skill names (directory names from ``.hcode/skills/``).
        """
        if not skills:
            self.console.print("[dim]No skills found in .hcode/skills/[/dim]")
            return

        table = Table(title="HCode Skills", box=box.ROUNDED, border_style="yellow")
        table.add_column("Skill Name", style="bold yellow")

        for name in skills:
            table.add_row(name)

        self.console.print(table)

    def show_workflows(self, workflows: list[str]) -> None:
        """Print a Rich table listing available HCode workflows.

        Args:
            workflows: List of workflow names (stem of ``.md`` files from
                ``.hcode/workflows/``).
        """
        if not workflows:
            self.console.print("[dim]No workflows found in .hcode/workflows/[/dim]")
            return

        table = Table(title="HCode Workflows", box=box.ROUNDED, border_style="cyan")
        table.add_column("Workflow Name", style="bold cyan")

        for name in workflows:
            table.add_row(name)

        self.console.print(table)
