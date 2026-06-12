"""Shared helpers for HCode v2 tools."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_root_dir() -> Path:
    return Path(os.environ.get("HCODE_ROOT_DIR", os.getcwd()))


def run_shell(cmd: str, *, cwd: Path | None = None, timeout: int = 30) -> tuple[int, str, str]:
    """Run a shell command; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd or get_root_dir()),
    )
    return result.returncode, result.stdout, result.stderr


def run_git(args: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a git sub-command; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        cwd=str(cwd or get_root_dir()),
    )
    return result.returncode, result.stdout, result.stderr
