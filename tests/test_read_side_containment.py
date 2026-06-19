"""Containment tests for the READ-side tools (glob / grep / ls).

The write-side tools (write/read/edit/multi_edit) already re-home leading-slash
and out-of-root paths into the working dir via ``files._resolve_path``. The
read-side tools did NOT: ``glob``/``grep`` (files.py) and ``ls`` (terminal.py)
used ``Path(path)`` as-is, so a model path like ``/greeting.py`` escaped to the
drive root — the agent wrote correctly but then verified at the wrong location and
looped. These tests pin that glob/grep/ls resolve paths through the SAME
containment as ``_resolve_path``.

Safety: the escape test uses a NON-EXISTENT target so no test ever scans a large
real directory tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hcode_v2.tools.files import _resolve_path, glob, grep
from hcode_v2.tools.terminal import ls


def _seed(tmp_path: Path, monkeypatch) -> Path:
    """Anchor HCODE_ROOT_DIR at tmp_path and create <root>/sub/a.py with a needle."""
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.py").write_text("needle here\n", encoding="utf-8")
    return sub


# --- FIX A: leading-slash paths re-home under the working root --------------


def test_glob_rehomes_leading_slash(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, monkeypatch)
    # "/sub" must resolve under the working root (-> <root>/sub), not the drive root
    result = glob.func(pattern="*.py", directory="/sub")
    assert "a.py" in result, f"glob did not search under the working root: {result!r}"


def test_grep_rehomes_leading_slash(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, monkeypatch)
    result = grep.func(pattern="needle", path="/sub")
    assert "needle" in result, f"grep did not search under the working root: {result!r}"


def test_ls_rehomes_leading_slash(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, monkeypatch)
    result = ls.func(path="/sub")
    assert "a.py" in result, f"ls did not list under the working root: {result!r}"


# --- FIX A: ".." traversal that escapes the root is rejected (like _resolve_path) ---


def test_read_side_rejects_dotdot_escape(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, monkeypatch)
    escape = "../../nope_escape_zzz"  # non-existent; resolves ABOVE the root
    for label, result in (
        ("glob", glob.func(pattern="*", directory=escape)),
        ("grep", grep.func(pattern="needle", path=escape)),
        ("ls", ls.func(path=escape)),
    ):
        assert "escapes the working directory" in result.lower(), (
            f"{label} did not reject the '..' escape: {result!r}"
        )


# --- FIX A: a normal relative path resolves UNDER the working root ----------


def test_read_side_relative_path_under_root(tmp_path: Path, monkeypatch) -> None:
    # A relative path ("sub") must resolve under HCODE_ROOT_DIR for all three —
    # the same anchoring write/read already apply (glob/ls are cwd-relative today,
    # so this is part of the fix, not just a guard).
    _seed(tmp_path, monkeypatch)
    assert "a.py" in glob.func(pattern="*.py", directory="sub")
    assert "needle" in grep.func(pattern="needle", path="sub")
    assert "a.py" in ls.func(path="sub")


# --- FIX B (DEFERRED): tool-call loop detection -----------------------------


@pytest.mark.skip(
    reason="FIX B deferred — decide after confirming FIX A stops the loop live. "
    "Intended: an HCode wrap_tool_call middleware that hashes repeated identical "
    "(tool_name, args) calls and short-circuits a verify loop (read/ls) instead of "
    "burning the full PEV iteration cap (~114s). PEV's own loop detector only "
    "hashes prose turns, so tool-call-only loops slip through."
)
def test_tool_loop_detection_short_circuits() -> None:  # pragma: no cover
    raise AssertionError("placeholder for FIX B tool-loop detection")
