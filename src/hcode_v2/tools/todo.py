"""Todo tools: todo_read, todo_write."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from langchain_core.tools import StructuredTool, tool
from pydantic import BaseModel

from hcode_v2.tools.base import get_root_dir

_TODO_FILE = ".hcode/task.md"
_TODO_RE = re.compile(r"^\s*-\s*\[([\s/xX])\]\s*(.*)")


@tool
def todo_read() -> str:
    """Read the current todo list from .hcode/task.md."""
    todo_path = get_root_dir() / _TODO_FILE
    if not todo_path.exists():
        return "No todo file found (.hcode/task.md)."
    try:
        lines = todo_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return f"Error reading todo file: {exc}"

    tasks: list[str] = []
    for line in lines:
        m = _TODO_RE.match(line)
        if m:
            marker, text = m.group(1), m.group(2).strip()
            if marker.strip().lower() in ("x",):
                status = "[x]"
            elif marker == "/":
                status = "[/]"
            else:
                status = "[ ]"
            tasks.append(f"{status} {text}")

    if not tasks:
        return "No tasks found in .hcode/task.md."
    return "\n".join(tasks)


class _TodoItem(BaseModel):
    text: str
    done: bool = False


class TodoWriteInput(BaseModel):
    todos: List[_TodoItem]
    append: bool = False


def _todo_write_fn(todos: List[_TodoItem], append: bool = False) -> str:
    """Write or replace the todo list in .hcode/task.md.

    Args:
        todos: List of {text, done} items to write.
        append: If True, append to existing file.
    """
    todo_path = get_root_dir() / _TODO_FILE
    todo_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"- [{'x' if t.done else ' '}] {t.text}" for t in todos]
    content = "\n".join(lines) + "\n"

    try:
        if append and todo_path.exists():
            existing = todo_path.read_text(encoding="utf-8")
            todo_path.write_text(existing + content, encoding="utf-8")
        else:
            todo_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing todo file: {exc}"

    action = "Appended" if append else "Wrote"
    return f"{action} {len(todos)} task(s) to {_TODO_FILE}."


todo_write = StructuredTool.from_function(
    func=_todo_write_fn,
    name="todo_write",
    description="Write or replace the todo list in .hcode/task.md.",
    args_schema=TodoWriteInput,
)
