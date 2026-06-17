"""Tests that the agent factory routes OFF the deepagents builtin file tools.

hcode's own ``edit`` / ``write`` / ``multi_edit`` now produce structured diff
artifacts (see ``tests/test_files_diff_artifact.py``). For the model to actually
use them, ``create_hcode_agent`` must stop exposing the deepagents builtins
``edit_file`` / ``write_file`` (injected by ``FilesystemMiddleware`` inside
``create_deep_agent``).

Inspection approach — why kwargs capture, not graph introspection:
  The exclusion is enforced by ``_ToolExclusionMiddleware`` at *model-call* time
  (``wrap_model_call`` filters ``request.tools``), NOT by removing the tool from
  the compiled graph's tool node — so the builtins are still bound, just hidden
  from the model each turn. A compiled-graph walk therefore can't prove the
  routing. The faithful, lightest probe is to capture the kwargs the factory
  hands to ``create_deep_agent`` and assert:
    * a ``_ToolExclusionMiddleware`` is installed whose excluded set covers the
      builtins (``edit_file``/``write_file``), and
    * hcode's own file tools are bound (``tools=``) and NOT in that excluded set.

  ``create_deep_agent`` and ``_build_model`` are replaced with recorders so no
  graph is compiled and no model/network is touched — mirroring how
  ``test_model_factory.py`` fakes the chat clients and how
  ``test_daemon_session_cache.py`` drives the async factory via ``asyncio.run``.

These FAIL today: the factory installs no ``_ToolExclusionMiddleware``, so the
builtins still reach the model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware

from hcode_v2.agent import factory

# deepagents builtins injected by FilesystemMiddleware — must be hidden so the
# model reaches for hcode's artifact-producing tools instead.
_BUILTIN_FILE_TOOLS = ("edit_file", "write_file")
# hcode's own registry tools that replace them.
_HCODE_FILE_TOOLS = ("edit", "write", "multi_edit")


def _capture_create_deep_agent_kwargs(monkeypatch, tmp_path: Path) -> dict:
    """Build the agent with create_deep_agent/_build_model faked; return the
    kwargs the factory passed to create_deep_agent (``tools``, ``middleware``…)."""
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()  # sentinel — only the kwargs matter here

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(factory, "_build_model", lambda: object())
    # create_hcode_agent assigns os.environ["HCODE_ROOT_DIR"] directly; pre-seed
    # via monkeypatch so it's restored at teardown and doesn't leak to other tests.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))

    # Real skills/workflows dirs so the middleware constructors have something to
    # scan; a missing MCP config path makes MCPClientManager.is_configured False.
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "workflows").mkdir(exist_ok=True)

    async def _build() -> None:
        await factory.create_hcode_agent(
            persist=False,  # MemorySaver — no sqlite file on disk
            mcp_config=str(tmp_path / "no_such_mcp.json"),
            skills_dir=str(tmp_path / "skills"),
            workflows_dir=str(tmp_path / "workflows"),
            work_dir=str(tmp_path),
        )

    asyncio.run(_build())
    return captured


def _excluded_tool_names(middleware: list) -> set[str]:
    """Union of all names excluded by every _ToolExclusionMiddleware in the stack."""
    names: set[str] = set()
    for mw in middleware:
        if isinstance(mw, _ToolExclusionMiddleware):
            names |= set(getattr(mw, "_excluded", frozenset()))
    return names


def test_agent_excludes_deepagents_builtin_edit_write(tmp_path, monkeypatch) -> None:
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    middleware = captured["middleware"]

    exclusions = [m for m in middleware if isinstance(m, _ToolExclusionMiddleware)]
    assert exclusions, "factory installs no _ToolExclusionMiddleware — builtins still reach the model"

    excluded = _excluded_tool_names(middleware)
    for name in _BUILTIN_FILE_TOOLS:
        assert name in excluded, (
            f"deepagents builtin '{name}' is not excluded — the model can still call it "
            f"instead of hcode's artifact-producing tool"
        )


def test_agent_includes_hcode_file_tools(tmp_path, monkeypatch) -> None:
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    names = {getattr(t, "name", None) for t in captured["tools"]}

    for name in _HCODE_FILE_TOOLS:
        assert name in names, f"hcode '{name}' tool is not bound to the agent"

    # ...and the routing must not accidentally exclude hcode's own tools.
    excluded = _excluded_tool_names(captured["middleware"])
    for name in _HCODE_FILE_TOOLS:
        assert name not in excluded, f"hcode '{name}' tool is wrongly excluded"


def test_hcode_edit_tools_carry_artifact_format(tmp_path, monkeypatch) -> None:
    # Ties the routing to the artifact work: the file tools the agent now uses
    # are exactly the ones that emit (content, artifact).
    captured = _capture_create_deep_agent_kwargs(monkeypatch, tmp_path)
    by_name = {getattr(t, "name", None): t for t in captured["tools"]}

    for name in _HCODE_FILE_TOOLS:
        tool = by_name.get(name)
        assert tool is not None, f"hcode '{name}' tool missing from the agent's tools"
        assert getattr(tool, "response_format", None) == "content_and_artifact", (
            f"hcode '{name}' must declare response_format='content_and_artifact'"
        )
