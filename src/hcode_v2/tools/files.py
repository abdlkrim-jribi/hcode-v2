"""File system tools: read, write, edit, multi_edit, glob, grep."""

from __future__ import annotations

import difflib
import fnmatch
import re
from pathlib import Path
from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel

from hcode_v2.tools.base import get_root_dir

_MAX_READ_LINES = 800


@tool
def read(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read a file and return its contents, optionally limited to a line range.

    Args:
        path: Relative or absolute path to the file.
        start_line: First line to include (1-based, inclusive).
        end_line: Last line to include (1-based, inclusive).
    """
    target = Path(path) if Path(path).is_absolute() else get_root_dir() / path
    if not target.exists():
        return f"Error: file not found: {path}"
    try:
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as exc:
        return f"Error reading file: {exc}"

    if start_line is not None or end_line is not None:
        s = (start_line or 1) - 1
        e = end_line if end_line is not None else len(lines)
        lines = lines[s:e]
    elif len(lines) > _MAX_READ_LINES:
        lines = lines[:_MAX_READ_LINES]

    numbered = "".join(f"{i + 1:4}: {l}" for i, l in enumerate(lines))
    return numbered


@tool
def write(path: str, content: str, append: bool = False) -> str:
    """Write content to a file, overwriting or appending.

    Args:
        path: Relative or absolute path to the file.
        content: Text to write.
        append: If True, append instead of overwrite.
    """
    target = Path(path) if Path(path).is_absolute() else get_root_dir() / path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        action = "Appended to" if append else "Wrote"
        return f"{action} {target} ({len(content)} characters)"
    except Exception as exc:
        return f"Error writing file: {exc}"


@tool
def edit(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact string in a file (first occurrence).

    Args:
        path: Relative or absolute path to the file.
        old_string: Exact text to find and replace.
        new_string: Replacement text.
    """
    target = Path(path) if Path(path).is_absolute() else get_root_dir() / path
    if not target.exists():
        return f"Error: file not found: {path}"
    try:
        original = target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading file: {exc}"

    if old_string not in original:
        # fuzzy fallback: strip leading whitespace per line
        stripped_old = "\n".join(l.strip() for l in old_string.splitlines())
        stripped_src = "\n".join(l.strip() for l in original.splitlines())
        if stripped_old not in stripped_src:
            return f"Error: old_string not found in {path}"
        # rebuild with stripped match — just do a simple replace on the stripped version
        new_content = original.replace(old_string.strip(), new_string.strip(), 1)
    else:
        new_content = original.replace(old_string, new_string, 1)

    try:
        target.write_text(new_content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing file: {exc}"

    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    return "".join(diff) if diff else "No changes made."


class _EditOperation(BaseModel):
    old_string: str
    new_string: str


class MultiEditInput(BaseModel):
    path: str
    edits: List[_EditOperation]


def _multi_edit_fn(path: str, edits: List[_EditOperation]) -> str:
    """Apply multiple sequential string replacements to a file.

    Args:
        path: Relative or absolute path to the file.
        edits: List of {old_string, new_string} operations applied in order.
    """
    target = Path(path) if Path(path).is_absolute() else get_root_dir() / path
    if not target.exists():
        return f"Error: file not found: {path}"
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading file: {exc}"

    original = content
    applied = 0
    for op in edits:
        if op.old_string in content:
            content = content.replace(op.old_string, op.new_string, 1)
            applied += 1
        else:
            return f"Error: old_string not found (edit #{applied + 1}): {op.old_string[:60]!r}"

    try:
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing file: {exc}"

    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    return "".join(diff) if diff else f"Applied {applied} edit(s), no net change."


from langchain_core.tools import StructuredTool

multi_edit = StructuredTool.from_function(
    func=_multi_edit_fn,
    name="multi_edit",
    description="Apply multiple sequential string replacements to a file.",
    args_schema=MultiEditInput,
)


@tool
def glob(pattern: str, directory: Optional[str] = None) -> str:
    """Find files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g. '**/*.py').
        directory: Directory to search in (default: project root).
    """
    root = Path(directory) if directory else get_root_dir()
    if not root.exists():
        return f"Error: directory not found: {root}"
    try:
        matches = sorted(str(p.relative_to(root)) for p in root.glob(pattern))
    except Exception as exc:
        return f"Error: {exc}"
    if not matches:
        return f"No files matching '{pattern}'"
    return "\n".join(matches)


@tool
def grep(pattern: str, path: Optional[str] = None, include: Optional[str] = None) -> str:
    """Search for a regex pattern in files.

    Args:
        pattern: Regular expression to search for.
        path: File or directory to search (default: project root).
        include: Glob pattern to filter files (e.g. '*.py').
    """
    root = get_root_dir()
    target = Path(path) if path else root
    if not target.is_absolute():
        target = root / target

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Invalid regex: {exc}"

    results: list[str] = []

    def _search_file(fp: Path) -> None:
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    rel = fp.relative_to(root) if fp.is_relative_to(root) else fp
                    results.append(f"{rel}:{i}: {line}")
        except Exception:
            pass

    if target.is_file():
        _search_file(target)
    else:
        for fp in sorted(target.rglob("*")):
            if not fp.is_file():
                continue
            if include and not fnmatch.fnmatch(fp.name, include):
                continue
            _search_file(fp)

    if not results:
        return f"No matches for '{pattern}'"
    return "\n".join(results[:500])
