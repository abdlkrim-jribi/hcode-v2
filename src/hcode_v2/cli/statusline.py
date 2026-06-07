"""A single, subtle status line for the HCode v2 CLI.

Reads real state only — provider/model from :class:`Config`, the git branch from
a short, best-effort subprocess — and never fabricates values it cannot read
(e.g. context-usage % is omitted unless explicitly supplied). Presentation only.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse

from rich.text import Text

from hcode_v2.utils.config import Config

_SEPARATOR = "  ·  "


def _provider_name(config: Config) -> str:
    """Derive a provider label from the resolved config, mirroring the factory.

    Anthropic is used when ``ANTHROPIC_API_KEY`` is set and no OpenAI-compatible
    key resolved; a custom ``base_url`` surfaces as its host (e.g. a self-hosted
    gpt-oss endpoint); otherwise it is plain OpenAI.
    """
    if os.getenv("ANTHROPIC_API_KEY") and not config.api_key:
        return "anthropic"
    if config.base_url:
        return urlparse(config.base_url).hostname or "custom"
    return "openai"


def _git_branch(workdir: str | None = None) -> str | None:
    """Return the current git branch, or ``None`` if not a repo / git is absent.

    Best-effort and fast: a failed command (not a repo, no git, timeout) yields
    ``None`` so the caller simply omits the segment.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workdir or None,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except Exception:  # noqa: BLE001 — never let the status line break the CLI
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def render_status(
    *,
    mode: str | None = None,
    context_pct: float | None = None,
    workdir: str | None = None,
) -> Text:
    """Build the subtle status line: provider · model · branch · mode · ctx%.

    Args:
        mode: Run mode label (e.g. ``"PEV"`` or ``"Fast"``), omitted if ``None``.
        context_pct: Context-window usage percentage; omitted if ``None`` so the
            line never shows a faked value.
        workdir: Directory to resolve the git branch from (defaults to cwd).

    Returns:
        A single dim Rich :class:`~rich.text.Text` line.
    """
    config = Config.from_env()
    line = Text(style="dim")

    line.append(_provider_name(config), style="green")
    line.append(_SEPARATOR, style="dim")
    line.append(config.model, style="cyan")

    branch = _git_branch(workdir)
    if branch:
        line.append(_SEPARATOR, style="dim")
        line.append(f"⎇ {branch}", style="magenta")

    if mode:
        line.append(_SEPARATOR, style="dim")
        line.append(mode, style="yellow")

    if context_pct is not None:
        line.append(_SEPARATOR, style="dim")
        line.append(f"ctx {context_pct:.0f}%", style="blue")

    return line


__all__ = ["render_status"]
