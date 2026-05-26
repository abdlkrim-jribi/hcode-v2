"""Tool registry — returns all HCode v2 tools as a flat list."""

from __future__ import annotations

from langchain_core.tools import BaseTool


def get_all_tools() -> list[BaseTool]:
    """Return every registered HCode v2 tool."""
    from hcode_v2.tools.diff import apply_patch, diff_files
    from hcode_v2.tools.files import edit, glob, grep, multi_edit, read, write
    from hcode_v2.tools.git import (
        git_add,
        git_branch,
        git_checkout,
        git_commit,
        git_diff,
        git_log,
        git_status,
    )
    from hcode_v2.tools.interactive import ask_user, confirm, display_panel
    from hcode_v2.tools.notebook import notebook_edit, notebook_execute, notebook_read
    from hcode_v2.tools.terminal import bash, bash_output, kill_shell, ls, search_output
    from hcode_v2.tools.todo import todo_read, todo_write
    from hcode_v2.tools.web import web_fetch, web_scrape, web_search

    return [
        # files (6)
        read,
        write,
        edit,
        multi_edit,
        glob,
        grep,
        # git (7)
        git_status,
        git_diff,
        git_add,
        git_commit,
        git_log,
        git_checkout,
        git_branch,
        # terminal (5)
        bash,
        bash_output,
        kill_shell,
        ls,
        search_output,
        # web (3)
        web_fetch,
        web_search,
        web_scrape,
        # interactive (3)
        ask_user,
        confirm,
        display_panel,
        # diff (2)
        diff_files,
        apply_patch,
        # todo (2)
        todo_read,
        todo_write,
        # notebook (3)
        notebook_read,
        notebook_edit,
        notebook_execute,
    ]
