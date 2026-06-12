"""Diff tools: diff_files, apply_patch."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from hcode_v2.tools.base import get_root_dir


@tool
def diff_files(path_a: str, path_b: str, context_lines: int = 3) -> str:
    """Show a unified diff between two files.

    Args:
        path_a: First file path.
        path_b: Second file path.
        context_lines: Lines of context around each change (default 3).
    """
    root = get_root_dir()

    def _resolve(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else root / q

    fa, fb = _resolve(path_a), _resolve(path_b)
    for fp, label in ((fa, path_a), (fb, path_b)):
        if not fp.exists():
            return f"Error: file not found: {label}"

    try:
        lines_a = fa.read_text(encoding="utf-8").splitlines(keepends=True)
        lines_b = fb.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as exc:
        return f"Error reading files: {exc}"

    diff = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=f"a/{path_a}",
        tofile=f"b/{path_b}",
        n=context_lines,
    ))
    return "".join(diff) if diff else "Files are identical."


@tool
def apply_patch(patch: str, directory: Optional[str] = None) -> str:
    """Apply a unified diff patch using the system `patch` command.

    Args:
        patch: Unified diff patch content (as returned by diff_files).
        directory: Directory to apply the patch in (default: project root).
    """
    import tempfile
    import os

    work_dir = Path(directory) if directory else get_root_dir()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, encoding="utf-8") as tf:
        tf.write(patch)
        patch_file = tf.name

    try:
        result = subprocess.run(
            ["patch", "-p1", "-i", patch_file],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(work_dir),
            timeout=30,
        )
        if result.returncode != 0:
            return f"Patch failed:\n{result.stderr or result.stdout}"
        return result.stdout or "Patch applied successfully."
    except FileNotFoundError:
        return "Error: 'patch' command not found. Install patch utility."
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        os.unlink(patch_file)
