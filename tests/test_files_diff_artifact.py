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


# --- leading-slash path resolution (working-dir hardening) ------------------
#
# Under virtual_mode=False the model sometimes emits a leading-slash path like
# "/app.py". Today the tools do ``Path(path) if Path(path).is_absolute() else
# get_root_dir() / path`` — on Windows "/app.py" is NOT absolute (no drive), so
# ``WindowsPath(root) / "/app.py"`` collapses to the DRIVE ROOT (C:\app.py),
# escaping the working dir. We harden the resolver so a leading-slash path is
# treated as ROOT-RELATIVE: strip the leading slash(es) and join under
# get_root_dir(), landing inside the working dir. Real OS-absolute paths
# (Windows "C:\..." here) are still honored as-is.
#
# HCODE_ROOT_DIR is pointed at tmp_path so get_root_dir() == the temp working dir.


def test_write_leading_slash_path_stays_in_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    write.func(path="/probe.py", content="x = 1\n")
    # Must land INSIDE the working dir, not at the drive root.
    target = tmp_path / "probe.py"
    assert target.is_file(), "leading-slash write escaped the working dir (drive root?)"
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_write_nested_leading_slash_in_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    write.func(path="/sub/probe.py", content="y = 2\n")
    target = tmp_path / "sub" / "probe.py"
    assert target.is_file(), "nested leading-slash write escaped the working dir"
    assert target.read_text(encoding="utf-8") == "y = 2\n"


def test_write_relative_path_unchanged(tmp_path: Path, monkeypatch) -> None:
    # Regression guard: a normal relative path keeps resolving under the root.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    write.func(path="probe.py", content="z = 3\n")
    target = tmp_path / "probe.py"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "z = 3\n"


def test_edit_leading_slash_path_stays_in_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    in_root = tmp_path / "e.py"
    in_root.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    edit.func(path="/e.py", old_string="beta", new_string="BETA_CHANGED")

    # The edit must have applied to the in-root file (not failed on a drive-root
    # path that doesn't exist).
    assert in_root.read_text(encoding="utf-8") == "alpha\nBETA_CHANGED\ngamma\n"


def test_real_absolute_path_respected(tmp_path: Path, monkeypatch) -> None:
    # A genuine OS-absolute path is still used verbatim (not re-rooted): the
    # hardening only re-homes leading-slash/relative paths.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "abs.py"
    write.func(path=str(target), content="a = 0\n")  # str(target) is absolute
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "a = 0\n"
