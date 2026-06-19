"""Rich diffusion feed — SLICE 1 tests: plumbing + WRITE panel + BASH line.

The feed currently renders a single ``✓ <verb> <path> (+A,-D)`` line (+ artifact
diff). This slice enriches it, purely in live.py, using data already in the events:

* WRITE (new file): a labeled panel — header ``Wrote <file>``, numbered content
  from the on_tool_start ``content`` arg (head/tail-truncated past ~10 lines), and
  a ``(N bytes written)`` footer (``len(content.encode())``).
* BASH: the ``execute`` tool currently renders a bare ``✓ execute``. Show
  ``Bash``/``Ran`` + the command (from the on_tool_start ``command`` arg) and the
  output (from the on_tool_end ToolMessage ``.content``), head/tail-truncated.

EDIT (slice 2) and READ/LS stay as-is — guarded here as regressions.

Tests are style-agnostic: they assert on text/structure, NOT colour codes.
These FAIL now: the full args aren't captured, WRITE is one line not a panel, and
BASH is bare ``✓ execute``.
"""

from __future__ import annotations

from types import SimpleNamespace

from rich.console import Console as RichConsole

from hcode_v2.cli.live import LiveTurnRenderer


def _tool_start(name: str, **tool_input) -> dict:
    return {"event": "on_tool_start", "name": name, "data": {"input": dict(tool_input)}}


def _tool_end(name: str, content: str = "", artifact: dict | None = None) -> dict:
    output = (
        SimpleNamespace(content=content)
        if artifact is None
        else SimpleNamespace(content=content, artifact=artifact)
    )
    return {"event": "on_tool_end", "name": name, "data": {"output": output}}


def _feed_text(*events: dict) -> str:
    """Drive events through a renderer (no Live) and return the rendered feed text."""
    console = RichConsole(record=True, width=100)
    renderer = LiveTurnRenderer(console=console)
    for event in events:
        renderer.process_event(event)
    console.print(renderer.renderable())
    return console.export_text()


# A representative write artifact (path/counts); the PANEL body comes from the
# on_tool_start "content" arg, not the diff.
def _write_artifact(path: str, additions: int) -> dict:
    plus = "\n".join(f"+x{i}" for i in range(additions))
    return {"diff": f"--- a/{path}\n+++ b/{path}\n@@ -0,0 @@\n{plus}",
            "additions": additions, "deletions": 0, "path": path}


# --- WRITE panel -----------------------------------------------------------


def test_write_panel_numbered_content() -> None:
    out = _feed_text(
        _tool_start("write", file_path="greeting.py", content="a\nb\nc"),
        _tool_end("write", artifact=_write_artifact("greeting.py", 3)),
    )
    assert "Wrote greeting.py" in out
    # numbered content body ("N │ <line>")
    assert "│ a" in out
    assert "│ b" in out
    assert "│ c" in out


def test_write_panel_byte_count() -> None:
    out = _feed_text(
        _tool_start("write", file_path="greeting.py", content="a\nb\nc"),  # 5 bytes
        _tool_end("write", artifact=_write_artifact("greeting.py", 3)),
    )
    assert "5 bytes" in out  # len("a\nb\nc".encode()) == 5


def test_write_header_has_file_icon() -> None:
    out = _feed_text(
        _tool_start("write", file_path="greeting.py", content="a\nb\nc"),
        _tool_end("write", artifact=_write_artifact("greeting.py", 3)),
    )
    assert "\U0001F4C4" in out          # 📄 prefixes the WRITE header
    assert "Wrote greeting.py" in out   # verb+filename unchanged by the icon


def test_write_panel_truncates_long() -> None:
    content = "\n".join(f"line{i:02d}" for i in range(1, 16))  # 15 lines
    out = _feed_text(
        _tool_start("write", file_path="big.py", content=content),
        _tool_end("write", artifact=_write_artifact("big.py", 15)),
    )
    assert "lines hidden" in out          # truncation marker
    assert "line02" in out                # head shown
    assert "line15" in out                # tail shown
    assert "line08" not in out            # middle hidden


# --- BASH line -------------------------------------------------------------


def test_bash_shows_command() -> None:
    cmd = "python -m py_compile x.py"
    out = _feed_text(
        _tool_start("execute", command=cmd),
        _tool_end("execute", content="compiled ok"),
    )
    assert ("Bash" in out) or ("Ran" in out), "bash must not render the bare tool name"
    assert "py_compile x.py" in out       # the command text is shown
    assert out.strip() != "✓ execute"     # not the old bare line


def test_bash_shows_output() -> None:
    out = _feed_text(
        _tool_start("execute", command="echo hi"),
        _tool_end("execute", content="hello from bash"),
    )
    assert "hello from bash" in out


def test_bash_header_has_run_icon() -> None:
    cmd = "python -m py_compile x.py"
    out = _feed_text(
        _tool_start("execute", command=cmd),
        _tool_end("execute", content="compiled ok"),
    )
    assert "▶" in out             # ▶ prefixes the BASH header
    assert "Bash" in out
    assert "py_compile x.py" in out    # command text unchanged by the icon


def test_bash_no_output() -> None:
    out = _feed_text(
        _tool_start("execute", command="touch f"),
        _tool_end("execute", content=""),
    )
    assert "(no output)" in out


# --- regressions (unchanged this slice) ------------------------------------


def test_edit_still_renders_diff() -> None:
    out = _feed_text(
        _tool_start("edit", path="calc.py"),
        _tool_end("edit", artifact={
            "diff": "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-old line\n+new line",
            "additions": 1, "deletions": 1, "path": "calc.py",
        }),
    )
    assert "Edited calc.py" in out or "Edited" in out
    assert "(+1, -1)" in out
    assert "old line" in out and "new line" in out
    assert "@@" not in out


def test_read_still_compact() -> None:
    out = _feed_text(
        _tool_start("read_file", file_path="x.py"),
        _tool_end("read_file", content="   1: data"),
    )
    assert "Read" in out and "x.py" in out
    # compact: no numbered-content panel, no byte footer, no read output body
    assert "│" not in out
    assert "bytes" not in out
    assert "1: data" not in out


def test_read_has_no_action_icon() -> None:
    out = _feed_text(
        _tool_start("read_file", file_path="x.py"),
        _tool_end("read_file", content="   1: data"),
    )
    # icons are per-action (WRITE/BASH only) — reads stay compact "✓ Read x.py".
    assert "\U0001F4C4" not in out     # no 📄
    assert "▶" not in out              # no ▶
