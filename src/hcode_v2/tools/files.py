"""File system tools: read, write, edit, multi_edit, glob, grep."""

from __future__ import annotations

import difflib
import fnmatch
import re
from pathlib import Path, PureWindowsPath
from typing import List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel

from hcode_v2.tools.base import get_root_dir

_MAX_READ_LINES = 800


class PathEscapeError(ValueError):
    """A tool path resolves outside the working directory."""


def _diff_counts(diff_lines: List[str]) -> tuple[int, int]:
    """Count added/removed content lines in a unified diff.

    Additions are ``+`` lines excluding the ``+++`` file header; deletions are
    ``-`` lines excluding the ``---`` file header.
    """
    additions = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    return additions, deletions


def _diff_artifact(diff: str, additions: int, deletions: int, path: str) -> dict:
    """Structured diff artifact carried on the tool's ToolMessage."""
    return {"diff": diff, "additions": additions, "deletions": deletions, "path": path}


def _resolve_path(path: str) -> Path:
    """Resolve a tool path argument to a real filesystem path, contained in root.

    Unified, cross-platform rule (root is :func:`get_root_dir`, resolved):

    * A genuine OS-absolute path that already sits INSIDE root is honored as-is
      (e.g. ``<root>/sub/x.py`` given absolutely).
    * A leading-slash/backslash path (``/app.py``, ``\\app.py``) — and a POSIX
      absolute that lands outside root — is treated as ROOT-RELATIVE: the
      anchor/leading separators are stripped and the remainder joined under
      root, so it lands INSIDE the working dir on every OS (not the drive root
      on Windows, not the filesystem root on Linux).
    * A real Windows drive/UNC absolute pointing OUTSIDE root is rejected.
    * Any path that would escape root via ``..`` traversal is rejected.

    Raises:
        PathEscapeError: if the path resolves outside the working directory.
    """
    root = get_root_dir().resolve()
    p = Path(path)
    if p.is_absolute():
        candidate = p.resolve()
        if candidate == root or candidate.is_relative_to(root):
            return candidate  # in-root absolute → honor as-is
        # Absolute OUTSIDE root:
        if PureWindowsPath(path).drive:
            # real Windows drive/UNC anchor (e.g. C:\Windows, \\srv\share) → reject
            raise PathEscapeError(path)
        # POSIX-absolute / leading-slash "virtual root" → re-home under root
        cleaned = path.replace("\\", "/").lstrip("/")
    else:
        # relative OR leading-slash/backslash driveless → root-relative
        cleaned = path.replace("\\", "/").lstrip("/")

    candidate = (root / cleaned).resolve()
    # Universal containment guard — catches every ".." escape on both OSes.
    if candidate != root and not candidate.is_relative_to(root):
        raise PathEscapeError(path)
    return candidate


@tool
def read(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read a file and return its contents, optionally limited to a line range.

    Args:
        path: Relative or absolute path to the file.
        start_line: First line to include (1-based, inclusive).
        end_line: Last line to include (1-based, inclusive).
    """
    try:
        target = _resolve_path(path)
    except PathEscapeError:
        return f"Error: path escapes the working directory: {path}"
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


@tool(response_format="content_and_artifact")
def write(path: str, content: str, append: bool = False) -> tuple[str, dict]:
    """Write content to a file, overwriting or appending.

    Args:
        path: Relative or absolute path to the file.
        content: Text to write.
        append: If True, append instead of overwrite.
    """
    try:
        target = _resolve_path(path)
    except PathEscapeError:
        return f"Error: path escapes the working directory: {path}", _diff_artifact("", 0, 0, path)
    # Capture the prior content (if any) so an overwrite shows a real diff; a
    # brand-new file diffs against empty (every line is an addition).
    try:
        old_text = target.read_text(encoding="utf-8") if target.exists() else ""
    except Exception:
        old_text = ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(content)
    except Exception as exc:
        return f"Error writing file: {exc}", _diff_artifact("", 0, 0, path)

    new_text = (old_text + content) if append else content
    diff_lines = list(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))
    diff = "\n".join(diff_lines)
    additions, deletions = _diff_counts(diff_lines)
    action = "Appended to" if append else "Wrote"
    counts = f"+{additions}" if deletions == 0 else f"+{additions}, -{deletions}"
    content_msg = f"{action} {Path(path).name} ({counts})"
    return content_msg, _diff_artifact(diff, additions, deletions, path)


@tool(response_format="content_and_artifact")
def edit(path: str, old_string: str, new_string: str) -> tuple[str, dict]:
    """Replace an exact string in a file (first occurrence).

    Args:
        path: Relative or absolute path to the file.
        old_string: Exact text to find and replace.
        new_string: Replacement text.
    """
    try:
        target = _resolve_path(path)
    except PathEscapeError:
        return f"Error: path escapes the working directory: {path}", _diff_artifact("", 0, 0, path)
    if not target.exists():
        return f"Error: file not found: {path}", _diff_artifact("", 0, 0, path)
    try:
        original = target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading file: {exc}", _diff_artifact("", 0, 0, path)

    if old_string not in original:
        # fuzzy fallback: strip leading whitespace per line
        stripped_old = "\n".join(l.strip() for l in old_string.splitlines())
        stripped_src = "\n".join(l.strip() for l in original.splitlines())
        if stripped_old not in stripped_src:
            return f"Error: old_string not found in {path}", _diff_artifact("", 0, 0, path)
        # rebuild with stripped match — just do a simple replace on the stripped version
        new_content = original.replace(old_string.strip(), new_string.strip(), 1)
    else:
        new_content = original.replace(old_string, new_string, 1)

    try:
        target.write_text(new_content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing file: {exc}", _diff_artifact("", 0, 0, path)

    diff_lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    diff = "".join(diff_lines)
    additions, deletions = _diff_counts(diff_lines)
    name = Path(path).name
    if not diff:
        return f"No changes to {name}", _diff_artifact("", 0, 0, path)
    content = f"Edited {name} (+{additions}, -{deletions})"
    return content, _diff_artifact(diff, additions, deletions, path)


class _EditOperation(BaseModel):
    old_string: str
    new_string: str


class MultiEditInput(BaseModel):
    path: str
    edits: List[_EditOperation]


def _multi_edit_fn(path: str, edits: List[_EditOperation]) -> tuple[str, dict]:
    """Apply multiple sequential string replacements to a file.

    Args:
        path: Relative or absolute path to the file.
        edits: List of {old_string, new_string} operations applied in order.
    """
    try:
        target = _resolve_path(path)
    except PathEscapeError:
        return f"Error: path escapes the working directory: {path}", _diff_artifact("", 0, 0, path)
    if not target.exists():
        return f"Error: file not found: {path}", _diff_artifact("", 0, 0, path)
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading file: {exc}", _diff_artifact("", 0, 0, path)

    original = content
    applied = 0
    for op in edits:
        if op.old_string in content:
            content = content.replace(op.old_string, op.new_string, 1)
            applied += 1
        else:
            return (
                f"Error: old_string not found (edit #{applied + 1}): {op.old_string[:60]!r}",
                _diff_artifact("", 0, 0, path),
            )

    try:
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing file: {exc}", _diff_artifact("", 0, 0, path)

    diff_lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    diff = "".join(diff_lines)
    additions, deletions = _diff_counts(diff_lines)
    name = Path(path).name
    if not diff:
        return f"Applied {applied} edit(s) to {name}, no net change", _diff_artifact("", 0, 0, path)
    content_msg = f"Edited {name} (+{additions}, -{deletions})"
    return content_msg, _diff_artifact(diff, additions, deletions, path)


from langchain_core.tools import StructuredTool

multi_edit = StructuredTool.from_function(
    func=_multi_edit_fn,
    name="multi_edit",
    description="Apply multiple sequential string replacements to a file.",
    args_schema=MultiEditInput,
    response_format="content_and_artifact",
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
