import sys
sys.path.insert(0, "../../../../src")
from hcode_v2.tools.registry import get_all_tools
from deepagents.middleware.safety_guard import _DESTRUCTIVE_TOOLS, _FILE_TOOLS, _FILE_ARG
from deepagents.middleware.pev import _VERIFY_READONLY_TOOLS

def test_destructive_tool_names_exist_in_registry():
    tool_names = {t.name for t in get_all_tools()}
    for name in _DESTRUCTIVE_TOOLS:
        assert name in tool_names, f"_DESTRUCTIVE_TOOLS has '{name}' but no tool with that name exists"

def test_verify_readonly_tool_names_exist_in_registry():
    tool_names = {t.name for t in get_all_tools()}
    for name in _VERIFY_READONLY_TOOLS:
        assert name in tool_names, f"_VERIFY_READONLY_TOOLS has '{name}' but no tool with that name exists"

def test_file_tools_names_exist_in_registry():
    tools = {t.name: t for t in get_all_tools()}
    for name in _FILE_TOOLS:
        assert name in tools, f"_FILE_TOOLS has '{name}' but no tool with that name exists"
    # _FILE_ARG must be the real path parameter accepted by every file tool
    for name in _FILE_TOOLS:
        params = tools[name].args
        assert _FILE_ARG in params, (
            f"_FILE_ARG='{_FILE_ARG}' is not a parameter of tool '{name}' (params: {list(params)})"
        )
