"""Contract tests for the structured diff artifact on hcode's file tools.

We're moving ``edit`` / ``write`` / ``multi_edit`` (``hcode_v2.tools.files``) to
the OpenCode model: the TOOL itself produces the diff and the +/- counts, rather
than returning a plain string. The new return contract is a 2-tuple
``(content, artifact)`` where:

  * ``content``  — a human-readable sentence (str) mentioning the file path.
  * ``artifact`` — a dict with keys ``"diff"`` (unified-diff text or ``""``),
    ``"additions"`` (int), ``"deletions"`` (int) and ``"path"`` (str).

So langchain packs the artifact onto the resulting ``ToolMessage``, each tool
also declares ``response_format == "content_and_artifact"``.

Invocation style: the existing tool tests call ``tool.invoke({...})``, but with
``content_and_artifact`` a plain-dict ``invoke`` returns only the content. To
assert the raw 2-tuple the tool function produces we call the underlying
``.func`` directly (the same callable langchain wraps) — ``edit``/``write`` take
plain kwargs; ``multi_edit.func`` takes ``List[_EditOperation]``, so we build
those from the module's own schema model.

These FAIL today: the tools return plain strings, expose no artifact, and their
``response_format`` is still the default ``"content"``.
"""

from __future__ import annotations

from pathlib import Path

from hcode_v2.tools.files import _EditOperation, edit, multi_edit, write

# difflib.unified_diff emits real "+"/"-" content lines (and "+++"/"---" file
# headers). Assertions below lean on that actual format, kept tolerant.


def test_edit_returns_diff_artifact(tmp_path: Path) -> None:
    target = tmp_path / "calc.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = edit.func(path=str(target), old_string="beta", new_string="BETA_CHANGED")

    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result
    assert isinstance(content, str)
    assert "calc.py" in content  # the sentence names the file

    assert isinstance(artifact, dict)
    diff = artifact["diff"]
    assert isinstance(diff, str)
    # the +/- content lines from the unified diff
    assert "beta" in diff and "BETA_CHANGED" in diff
    assert any(l.startswith("-") and "beta" in l for l in diff.splitlines())
    assert any(l.startswith("+") and "BETA_CHANGED" in l for l in diff.splitlines())

    assert artifact["additions"] == 1
    assert artifact["deletions"] == 1
    assert artifact["path"].endswith("calc.py")


def test_write_returns_artifact(tmp_path: Path) -> None:
    target = tmp_path / "new_module.py"  # brand-new file
    body = "one\ntwo\nthree\n"  # 3 lines written

    result = write.func(path=str(target), content=body)

    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result
    assert isinstance(content, str)
    assert "new_module.py" in content

    assert isinstance(artifact, dict)
    assert isinstance(artifact["diff"], str)  # may be all-additions or ""
    assert artifact["additions"] == 3  # number of lines written
    assert artifact["deletions"] == 0  # a fresh write removes nothing
    assert artifact["path"].endswith("new_module.py")


def test_multi_edit_returns_artifact(tmp_path: Path) -> None:
    target = tmp_path / "multi.py"
    target.write_text("line one\nkeep me\nline three\n", encoding="utf-8")

    edits = [
        _EditOperation(old_string="line one", new_string="LINE ONE"),
        _EditOperation(old_string="line three", new_string="LINE THREE"),
    ]
    result = multi_edit.func(path=str(target), edits=edits)

    assert isinstance(result, tuple) and len(result) == 2
    content, artifact = result
    assert isinstance(content, str)
    assert "multi.py" in content

    assert isinstance(artifact, dict)
    diff = artifact["diff"]
    assert isinstance(diff, str)
    # both edits show up in the synthesized diff
    assert "LINE ONE" in diff and "LINE THREE" in diff
    # two distinct lines changed -> two additions and two deletions
    assert artifact["additions"] == 2
    assert artifact["deletions"] == 2
    assert artifact["path"].endswith("multi.py")


def test_edit_tool_response_format_is_content_and_artifact() -> None:
    # langchain only packs the artifact onto the ToolMessage when the tool
    # declares this response_format; today these are the default "content".
    for tool_obj in (edit, write, multi_edit):
        assert tool_obj.response_format == "content_and_artifact", (
            f"{tool_obj.name} must declare response_format='content_and_artifact'"
        )
