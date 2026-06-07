"""Startup banner and welcome block for the HCode v2 CLI.

Presentation only — these helpers build Rich renderables and never touch the
agent, factory, or any network. The look is a nod to v1: a large "HCODE" title
with a green→cyan gradient, a tagline, and a version line.
"""

from __future__ import annotations

from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

# Block-letter "HCODE" — a clean, self-contained ASCII title (no figlet dep).
_LOGO: str = r"""
██   ██  ██████  ██████  ██████  ███████
██   ██ ██      ██    ██ ██   ██ ██
███████ ██      ██    ██ ██   ██ █████
██   ██ ██      ██    ██ ██   ██ ██
██   ██  ██████  ██████  ██████  ███████
"""

# Gradient endpoints: pure green → cyan (the v1 "neon" feel, reimplemented).
_GRADIENT_START = (0x00, 0xFF, 0x00)  # green
_GRADIENT_END = (0x00, 0xCF, 0xFF)    # cyan


def _gradient_line(text: str) -> Text:
    """Color one line left→right with the green→cyan gradient (whitespace kept plain)."""
    line = Text()
    span = max(len(text) - 1, 1)
    for i, char in enumerate(text):
        if not char.strip():
            line.append(char)
            continue
        f = i / span
        r = round(_GRADIENT_START[0] + (_GRADIENT_END[0] - _GRADIENT_START[0]) * f)
        g = round(_GRADIENT_START[1] + (_GRADIENT_END[1] - _GRADIENT_START[1]) * f)
        b = round(_GRADIENT_START[2] + (_GRADIENT_END[2] - _GRADIENT_START[2]) * f)
        line.append(char, style=Style(color=f"#{r:02X}{g:02X}{b:02X}", bold=True))
    return line


def render_banner() -> RenderableType:
    """Build the startup banner: gradient "HCODE" title, tagline, and version line.

    Returns:
        A centered Rich renderable. The rendered text contains the literal
        ``HCODE`` and ``v2.0.0`` in the info line (the title itself is ASCII art).
    """
    art = Text()
    for raw in _LOGO.strip("\n").split("\n"):
        art.append_text(_gradient_line(raw))
        art.append("\n")

    separator = Text("━" * 41, style="bold #00CFAA")

    info = Text()
    info.append("HCODE ", style="bold #00FF88")
    info.append("v2.0.0", style="bold #00FFAA")
    info.append("  │  ", style="dim")
    info.append("AI-Powered Coding Agent", style="italic #00FFFF")
    info.append("  │  ", style="dim")
    info.append("DeepAgents + LangGraph", style="#39FF14")

    return Group(
        Align.center(art),
        Align.center(separator),
        Align.center(info),
    )


def render_welcome() -> RenderableType:
    """Build a short tips block shown at the start of an interactive session."""
    tips = Text()
    tips.append("• Type naturally — HCode understands context\n", style="dim")
    tips.append("• ", style="dim")
    tips.append("/help", style="bold cyan")
    tips.append(" for commands · ", style="dim")
    tips.append("/exit", style="bold cyan")
    tips.append(" to quit\n", style="dim")
    tips.append("• Press Ctrl+C to interrupt", style="dim")
    return Panel(tips, title="Welcome", border_style="green", expand=False)


__all__ = ["render_banner", "render_welcome"]
