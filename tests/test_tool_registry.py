"""Tests for the HCode v2 tool registry.

These assert the registry loads the full, expected tool set with no duplicate
names and no network access — `get_all_tools` only imports and returns tool
objects.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from hcode_v2.tools.registry import get_all_tools

# Expected tool names grouped by category, mirroring the registry layout.
_EXPECTED_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "files": ("read", "write", "edit", "multi_edit", "glob", "grep"),
    "git": (
        "git_status",
        "git_diff",
        "git_add",
        "git_commit",
        "git_log",
        "git_checkout",
        "git_branch",
    ),
    "terminal": ("bash", "bash_output", "kill_shell", "ls", "search_output"),
    "web": ("web_fetch", "web_search", "web_scrape"),
    "interactive": ("ask_user", "confirm", "display_panel"),
    "diff": ("diff_files", "apply_patch"),
    "todo": ("todo_read", "todo_write"),
    "notebook": ("notebook_read", "notebook_edit", "notebook_execute"),
    "lsp": ("check_diagnostics", "goto_definition", "find_references", "hover_info"),
}

_EXPECTED_NAMES: set[str] = {name for names in _EXPECTED_BY_CATEGORY.values() for name in names}
_EXPECTED_COUNT = 35


def test_registry_loads_all_35_tools() -> None:
    tools = get_all_tools()
    assert len(tools) == _EXPECTED_COUNT
    assert len(_EXPECTED_NAMES) == _EXPECTED_COUNT  # guards the expectation table itself


def test_every_tool_is_a_langchain_basetool() -> None:
    assert all(isinstance(tool, BaseTool) for tool in get_all_tools())


def test_tool_names_are_unique() -> None:
    names = [tool.name for tool in get_all_tools()]
    assert len(names) == len(set(names)), "duplicate tool name(s) in registry"


def test_registry_exposes_exactly_the_expected_tools() -> None:
    names = {tool.name for tool in get_all_tools()}
    assert names == _EXPECTED_NAMES


def test_every_tool_has_a_nonempty_description() -> None:
    # A bound tool with no description gives the model nothing to dispatch on.
    for tool in get_all_tools():
        assert tool.description and tool.description.strip(), f"{tool.name} has no description"
