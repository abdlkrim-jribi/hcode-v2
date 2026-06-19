"""Tests for the execution-time path-containment middleware.

The escape bug: the deepagents builtins (write_file/read_file/edit_file/ls/glob/
grep) are bound to the ToolNode but NOT containment-anchored, and the model calls
them from memory even when they're hidden from the menu — so a path like
``/greeting.py`` escaped to the drive root (``D:\\greeting.py``). Menu exclusion is
not enough (excluded-but-bound). The fix is an HCode-side ``AgentMiddleware`` whose
``wrap_tool_call``/``awrap_tool_call`` re-homes the path arg of EVERY file tool
through ``files._resolve_path`` at execution time — covering both the builtins and
hcode's own tools, regardless of menu state.

Path-arg keys per tool: builtins read_file/write_file/edit_file use ``file_path``;
builtin ls/glob/grep use ``path``; hcode read/write/edit/multi_edit/grep/ls use
``path``; hcode glob uses ``directory``. The middleware re-homes whichever of
``file_path``/``path``/``directory`` is present.

These FAIL now: ``hcode_v2.agent.path_containment`` does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import ToolCallRequest

from hcode_v2.agent.path_containment import _PathContainmentMiddleware


def _req(name: str, args: dict, tool_id: str = "tc1") -> ToolCallRequest:
    """Build a fake ToolCallRequest (all four fields are required)."""
    return ToolCallRequest(
        tool_call={"name": name, "args": dict(args), "id": tool_id},
        tool=None,
        state={},
        runtime=None,
    )


class _Spy:
    """Sync handler spy: records the request it received, returns a ToolMessage."""

    def __init__(self) -> None:
        self.called = False
        self.req: ToolCallRequest | None = None

    def __call__(self, request: ToolCallRequest) -> ToolMessage:
        self.called = True
        self.req = request
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])


def _resolved_arg(spy: _Spy, key: str) -> Path:
    return Path(spy.req.tool_call["args"][key])


# --- builtins (the escape vector) re-homed under root ----------------------


def test_builtin_write_file_leading_slash_rehomed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    result = mw.wrap_tool_call(
        _req("write_file", {"file_path": "/greeting.py", "contents": "x=1\n"}), spy
    )
    assert spy.called, "handler must run for an in-root (re-homed) write"
    p = _resolved_arg(spy, "file_path")
    assert p.is_relative_to(tmp_path.resolve()), f"escaped: {p}"
    assert p.name == "greeting.py"
    # non-path args are preserved
    assert spy.req.tool_call["args"]["contents"] == "x=1\n"
    assert result.content == "ok"


def test_builtin_read_file_leading_slash_rehomed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    mw.wrap_tool_call(_req("read_file", {"file_path": "/greeting.py"}), spy)
    assert spy.called
    p = _resolved_arg(spy, "file_path")
    assert p.is_relative_to(tmp_path.resolve()) and p.name == "greeting.py"


def test_builtin_ls_leading_slash_rehomed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    mw.wrap_tool_call(_req("ls", {"path": "/"}), spy)
    assert spy.called
    # "/" re-homes to the working root itself
    assert _resolved_arg(spy, "path") == tmp_path.resolve()


# --- ".." escape rejected without executing --------------------------------


def test_dotdot_escape_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    result = mw.wrap_tool_call(_req("write_file", {"file_path": "../../x.py"}), spy)
    assert spy.called is False, "an escaping write must NOT execute"
    assert isinstance(result, ToolMessage)
    assert "escapes the working directory" in result.content.lower()


# --- hcode's own tools contained too (uniform) ------------------------------


def test_hcode_write_also_contained(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    mw.wrap_tool_call(_req("write", {"path": "/x.py", "content": "y\n"}), spy)
    assert spy.called
    p = _resolved_arg(spy, "path")
    assert p.is_relative_to(tmp_path.resolve()) and p.name == "x.py"


def test_hcode_glob_directory_arg_rehomed(tmp_path, monkeypatch) -> None:
    # hcode glob uses the "directory" key (not "path")
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    mw.wrap_tool_call(_req("glob", {"pattern": "*.py", "directory": "/sub"}), spy)
    assert spy.called
    p = _resolved_arg(spy, "directory")
    assert p.is_relative_to(tmp_path.resolve()) and p.name == "sub"
    assert spy.req.tool_call["args"]["pattern"] == "*.py"  # non-path arg preserved


# --- non-file tools pass through untouched ---------------------------------


def test_non_file_tool_passthrough(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    mw.wrap_tool_call(_req("bash", {"command": "echo hi"}), spy)
    assert spy.called
    assert spy.req.tool_call["args"] == {"command": "echo hi"}  # untouched


# --- a normal relative path resolves under root ----------------------------


def test_relative_path_contained(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()
    spy = _Spy()
    mw.wrap_tool_call(_req("read", {"path": "sub/x.py"}), spy)
    assert spy.called
    p = _resolved_arg(spy, "path")
    assert p.is_relative_to(tmp_path.resolve())
    assert p == (tmp_path / "sub" / "x.py").resolve()


# --- the async seam (the agent uses awrap_tool_call) mirrors the sync one ---


async def test_awrap_rehomes_and_rejects(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    mw = _PathContainmentMiddleware()

    received: dict = {}

    async def ahandler(request: ToolCallRequest) -> ToolMessage:
        received["args"] = dict(request.tool_call["args"])
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

    # re-home
    await mw.awrap_tool_call(_req("write_file", {"file_path": "/g.py"}), ahandler)
    p = Path(received["args"]["file_path"])
    assert p.is_relative_to(tmp_path.resolve()) and p.name == "g.py"

    # escape rejected, handler not called
    received.clear()
    result = await mw.awrap_tool_call(_req("write_file", {"file_path": "../../e.py"}), ahandler)
    assert "args" not in received, "escaping write must NOT execute (async)"
    assert isinstance(result, ToolMessage)
    assert "escapes the working directory" in result.content.lower()
