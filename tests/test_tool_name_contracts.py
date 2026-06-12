"""Contract tests: every tool name in SafetyGuard/PEV constants must exist in the registry.

These tests are the guard that prevents tool-name drift between
``libs/deepagents`` middleware constants and the actual registered HCode tools.
They live here (root ``tests/``) rather than in ``libs/deepagents/tests/`` so
that ``hcode_v2`` is importable — the shell-test CI job (``uv run --group dev
pytest``) installs ``hcode_v2`` as an editable package, whereas the deepagents
venv does not.

This is why the drift introduced in ``b57a6d2`` went undetected: the original
copy lived in ``libs/deepagents/tests/`` with a fragile ``sys.path.insert`` hack
that silently failed in CI.
"""

from __future__ import annotations

from deepagents.middleware.pev import _VERIFY_READONLY_TOOLS
from deepagents.middleware.safety_guard import _DESTRUCTIVE_TOOLS, _FILE_ARG, _FILE_TOOLS
from hcode_v2.tools.registry import get_all_tools


def test_destructive_tool_names_exist_in_registry() -> None:
    """Every name in _DESTRUCTIVE_TOOLS must match a real registered tool."""
    tool_names = {t.name for t in get_all_tools()}
    for name in _DESTRUCTIVE_TOOLS:
        assert name in tool_names, (
            f"_DESTRUCTIVE_TOOLS has '{name}' but no tool with that name exists in the registry. "
            f"Either add the tool or remove it from _DESTRUCTIVE_TOOLS."
        )


def test_verify_readonly_tool_names_exist_in_registry() -> None:
    """Every name in _VERIFY_READONLY_TOOLS must match a real bound tool.

    The runtime tool set is TWO layers, not just the hcode registry:
    ``create_deep_agent`` always composes the deepagents builtin
    ``FilesystemMiddleware``, whose tools (``read_file`` etc.) are bound
    alongside ``get_all_tools()``. Probe-proven: the model uses ``read_file``
    during execute and reaches for it in verify, so the whitelist must cover
    both layers and this contract checks against their union.
    """
    from deepagents.middleware.filesystem import FilesystemMiddleware

    registry_names = {t.name for t in get_all_tools()}
    builtin_names = {t.name for t in FilesystemMiddleware().tools}
    tool_names = registry_names | builtin_names
    for name in _VERIFY_READONLY_TOOLS:
        assert name in tool_names, (
            f"_VERIFY_READONLY_TOOLS has '{name}' but no tool with that name exists in the "
            f"hcode registry or the deepagents builtin FilesystemMiddleware."
        )


def test_file_tools_names_exist_in_registry_with_correct_path_arg() -> None:
    """Every name in _FILE_TOOLS must exist in the registry and accept _FILE_ARG."""
    tools = {t.name: t for t in get_all_tools()}
    for name in _FILE_TOOLS:
        assert name in tools, (
            f"_FILE_TOOLS has '{name}' but no tool with that name exists in the registry."
        )
        params = tools[name].args
        assert _FILE_ARG in params, (
            f"_FILE_ARG='{_FILE_ARG}' is not a parameter of tool '{name}' "
            f"(actual params: {list(params)}). "
            f"Either fix the tool's parameter name or update _FILE_ARG."
        )
