"""Git tools: status, diff, add, commit, log, checkout, branch."""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from hcode_v2.tools.base import get_root_dir, run_git


@tool
def git_status() -> str:
    """Return the current git status of the repository."""
    rc, out, err = run_git(["status"])
    return out or err or "No output."


@tool
def git_diff(cached: bool = False, path: Optional[str] = None) -> str:
    """Show git diff.

    Args:
        cached: If True, show staged diff (--cached).
        path: Limit diff to a specific file or directory.
    """
    args = ["diff"]
    if cached:
        args.append("--cached")
    if path:
        args += ["--", path]
    rc, out, err = run_git(args)
    return out or err or "No diff."


@tool
def git_add(path: str = ".") -> str:
    """Stage files for commit.

    Args:
        path: File or directory to stage (default: all).
    """
    rc, out, err = run_git(["add", path])
    if rc != 0:
        return f"Error: {err}"
    return f"Staged: {path}"


@tool
def git_commit(message: str, all_changes: bool = False) -> str:
    """Create a git commit.

    Args:
        message: Commit message.
        all_changes: If True, stage all tracked changes before committing (-a).
    """
    args = ["commit"]
    if all_changes:
        args.append("-a")
    args += ["-m", message]
    rc, out, err = run_git(args)
    if rc != 0:
        return f"Error: {err or out}"
    return out or "Committed."


@tool
def git_log(max_count: int = 10, oneline: bool = True) -> str:
    """Show recent git log.

    Args:
        max_count: Number of commits to show.
        oneline: If True, one commit per line (--oneline).
    """
    args = ["log", f"-{max_count}"]
    if oneline:
        args.append("--oneline")
    rc, out, err = run_git(args)
    return out or err or "No commits."


@tool
def git_checkout(ref: str, new_branch: bool = False) -> str:
    """Checkout a branch or commit.

    Args:
        ref: Branch name, tag, or commit hash.
        new_branch: If True, create a new branch (-b).
    """
    args = ["checkout"]
    if new_branch:
        args.append("-b")
    args.append(ref)
    rc, out, err = run_git(args)
    if rc != 0:
        return f"Error: {err or out}"
    return out or err or f"Checked out {ref}."


@tool
def git_branch(list_all: bool = False, delete: Optional[str] = None) -> str:
    """List or delete git branches.

    Args:
        list_all: If True, list remote branches too (-a).
        delete: Branch name to delete.
    """
    if delete:
        rc, out, err = run_git(["branch", "-d", delete])
        if rc != 0:
            return f"Error: {err or out}"
        return out or f"Deleted branch {delete}."

    args = ["branch"]
    if list_all:
        args.append("-a")
    rc, out, err = run_git(args)
    return out or err or "No branches."
