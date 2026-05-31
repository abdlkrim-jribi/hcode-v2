"""Render tests for `HCodeDisplay`.

Output is captured from the display's own Rich `Console` via `capture()`, so the
tests assert on what the user would actually see without touching stdout.
"""

from __future__ import annotations

from hcode_v2.cli.display import HCodeDisplay


def _render(method_name: str, *args: object) -> str:
    display = HCodeDisplay()
    with display.console.capture() as capture:
        getattr(display, method_name)(*args)
    return capture.get()


def test_task_header_includes_task_text() -> None:
    out = _render("show_task_header", "build the thing")
    assert "build the thing" in out
    assert "HCode v2" in out


def test_result_is_rendered() -> None:
    out = _render("show_result", "all done")
    assert "all done" in out
    assert "Result" in out


def test_error_is_rendered() -> None:
    out = _render("show_error", "it broke")
    assert "it broke" in out
    assert "Error" in out


def test_phase_transition_uppercases_phase() -> None:
    out = _render("show_phase_transition", "plan")
    assert "PLAN" in out


def test_skills_empty_and_populated() -> None:
    assert "No skills" in _render("show_skills", [])
    populated = _render("show_skills", ["refactor", "review"])
    assert "refactor" in populated
    assert "review" in populated


def test_workflows_empty_and_populated() -> None:
    assert "No workflows" in _render("show_workflows", [])
    assert "release" in _render("show_workflows", ["release"])


def test_mcp_servers_empty_and_populated() -> None:
    assert "No MCP servers" in _render("show_mcp_servers", [])
    populated = _render(
        "show_mcp_servers",
        [{"name": "github", "command": "npx ...", "status": "connected"}],
    )
    assert "github" in populated
