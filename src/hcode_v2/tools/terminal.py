"""Terminal tools: bash, ls, bash_output, search_output, kill_shell."""

from __future__ import annotations

import platform
import subprocess
import threading
import uuid
from typing import Optional

from langchain_core.tools import tool

from hcode_v2.tools.base import get_root_dir

_bg_shells: dict[str, dict] = {}

_WINDOWS_CMD_MAP = {
    "ls ": "dir ",
    "ls\n": "dir\n",
    "ls\r": "dir\r",
    "cp ": "copy ",
    "mv ": "move ",
    "rm ": "del ",
    "cat ": "type ",
    "clear": "cls",
    "touch ": "type nul > ",
    "which ": "where ",
    "mkdir -p ": "mkdir ",
}


def _maybe_translate(cmd: str) -> str:
    if platform.system() != "Windows":
        return cmd
    for unix_cmd, win_cmd in _WINDOWS_CMD_MAP.items():
        if cmd.startswith(unix_cmd) or cmd == unix_cmd.strip():
            return cmd.replace(unix_cmd, win_cmd, 1)
    return cmd


@tool
def bash(
    command: str,
    timeout: int = 30,
    background: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Execute a shell command.

    Args:
        command: Shell command to run.
        timeout: Seconds before timeout (default 30, ignored in background mode).
        background: If True, run in background and return a session ID.
        cwd: Working directory (default: project root).
    """
    translated = _maybe_translate(command)
    work_dir = str(cwd) if cwd else str(get_root_dir())

    if background:
        session_id = str(uuid.uuid4())[:8]
        output_lines: list[str] = []
        done_event = threading.Event()

        def _run() -> None:
            try:
                proc = subprocess.Popen(
                    translated,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    cwd=work_dir,
                )
                for line in proc.stdout:  # type: ignore[union-attr]
                    output_lines.append(line)
                proc.wait()
                _bg_shells[session_id]["returncode"] = proc.returncode
            finally:
                done_event.set()

        _bg_shells[session_id] = {"output": output_lines, "done": done_event, "returncode": None}
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return f"Background shell started. Session ID: {session_id}"

    try:
        result = subprocess.run(
            translated,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=work_dir,
        )
        out = result.stdout
        err = result.stderr
        combined = (out + err).strip()
        if result.returncode != 0:
            return f"Exit {result.returncode}\n{combined}" if combined else f"Exit {result.returncode}"
        return combined or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"


@tool
def bash_output(session_id: str) -> str:
    """Get current output from a background shell session.

    Args:
        session_id: Session ID returned by bash(background=True).
    """
    if session_id not in _bg_shells:
        return f"Error: unknown session '{session_id}'"
    info = _bg_shells[session_id]
    output = "".join(info["output"])
    done = info["done"].is_set()
    rc = info.get("returncode")
    status = f"[done, exit {rc}]" if done else "[running]"
    return f"{status}\n{output}" if output else status


@tool
def kill_shell(session_id: str) -> str:
    """Remove a background shell session record.

    Args:
        session_id: Session ID to remove.
    """
    if session_id not in _bg_shells:
        return f"Error: unknown session '{session_id}'"
    del _bg_shells[session_id]
    return f"Session {session_id} removed."


@tool
def ls(path: Optional[str] = None, all_files: bool = False) -> str:
    """List directory contents.

    Args:
        path: Directory to list (default: project root).
        all_files: If True, include hidden files.
    """
    from pathlib import Path

    target = Path(path) if path else get_root_dir()
    if not target.exists():
        return f"Error: path not found: {target}"
    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines: list[str] = []
        for entry in entries:
            if not all_files and entry.name.startswith("."):
                continue
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")
        return "\n".join(lines) if lines else "(empty)"
    except Exception as exc:
        return f"Error: {exc}"


@tool
def search_output(pattern: str, session_id: Optional[str] = None) -> str:
    """Search for a pattern in background shell output.

    Args:
        pattern: String to search for (case-insensitive).
        session_id: Session ID to search in. If omitted, searches the most recent session.
    """
    import re

    if session_id is None:
        if not _bg_shells:
            return "No background sessions."
        session_id = list(_bg_shells.keys())[-1]

    if session_id not in _bg_shells:
        return f"Error: unknown session '{session_id}'"

    output = "".join(_bg_shells[session_id]["output"])
    matches = [line for line in output.splitlines() if re.search(pattern, line, re.IGNORECASE)]
    if not matches:
        return f"No matches for '{pattern}' in session {session_id}"
    return "\n".join(matches)
