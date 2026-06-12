"""Regression tests for the terminal tools (hcode_v2.tools.terminal).

Windows encoding regression: child processes that write raw non-UTF-8 bytes
(e.g. pytest emitting cp1252/cp1256 output) must never crash the subprocess
reader thread with UnicodeDecodeError. The decode uses errors="replace", so
a stray byte degrades to U+FFFD instead of killing the run.
"""

from __future__ import annotations

import sys

from hcode_v2.tools.terminal import _bg_shells, bash, bash_output

# Writes a raw 0x82 byte (invalid UTF-8 start byte) straight to the stdout
# pipe via the buffer layer, bypassing PYTHONIOENCODING — guaranteed to reach
# the parent's decoder undecodable.
_RAW_BYTE_CMD = (
    f'"{sys.executable}" -c '
    '"import sys; sys.stdout.buffer.write(b\'hello \\x82\\n\')"'
)


def test_bash_survives_non_utf8_output() -> None:
    result = bash.invoke({"command": _RAW_BYTE_CMD})
    assert isinstance(result, str)
    assert "hello" in result
    assert not result.startswith("Error:")
    # The undecodable byte is replaced, not raised
    assert "�" in result


def test_background_bash_survives_non_utf8_output() -> None:
    result = bash.invoke({"command": _RAW_BYTE_CMD, "background": True})
    assert isinstance(result, str)
    session_id = result.rsplit(":", 1)[-1].strip()
    assert _bg_shells[session_id]["done"].wait(timeout=30)

    output = bash_output.invoke({"session_id": session_id})
    assert "hello" in output
    assert "�" in output
    assert "[done, exit 0]" in output
