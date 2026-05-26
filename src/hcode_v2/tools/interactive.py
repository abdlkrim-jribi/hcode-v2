"""Interactive tools: ask_user, confirm, display_panel."""

from __future__ import annotations

from typing import List, Optional

from langchain_core.tools import tool


@tool
def ask_user(question: str, choices: Optional[List[str]] = None) -> str:
    """Ask the user a question and wait for their response.

    Args:
        question: The question to ask.
        choices: Optional list of choices to present (displayed as a numbered list).
    """
    print(f"\n[HCode] {question}")
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        print("Enter your choice (number or text): ", end="", flush=True)
    else:
        print("Your answer: ", end="", flush=True)

    try:
        answer = input().strip()
    except (EOFError, KeyboardInterrupt):
        return "(no response)"

    if choices and answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    return answer


@tool
def confirm(prompt: str) -> str:
    """Ask the user for yes/no confirmation.

    Args:
        prompt: The confirmation question.
    """
    print(f"\n[HCode] {prompt} [y/N] ", end="", flush=True)
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    return "yes" if answer in ("y", "yes") else "no"


@tool
def display_panel(title: str, content: str, style: str = "blue") -> str:
    """Display a formatted panel to the user.

    Args:
        title: Panel title.
        content: Panel body text.
        style: Rich border style (e.g. 'blue', 'green', 'red').
    """
    try:
        from rich.console import Console
        from rich.panel import Panel

        Console(force_terminal=True).print(
            Panel(content, title=title, border_style=style, expand=False)
        )
    except ImportError:
        print(f"\n=== {title} ===\n{content}\n")
    return f"Displayed panel: {title}"
