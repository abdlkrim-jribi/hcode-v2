"""Tool-feedback integrity fixes for hcode_v2.tools.files (harness audit findings).

Three bugs the model's self-correction was silently relying on:

  C2 — read() mislabeled ranged reads from line 1 instead of the real position,
       so a diagnostic at "line 105" was cross-referenced against a read that
       had relabeled it "line 6".
  C3 — read() truncated at 800 lines with NO marker, so the model believed the
       file ended there and re-appended code that already existed past it.
  T2 — edit() gave an opaque "old_string not found" on a miss, AND its fuzzy
       whitespace fallback was broken: the membership check stripped whitespace
       per-line, but the replace only stripped the whole block's outer ends —
       an internal indentation mismatch passed the check, then silently
       no-op'd, returning "No changes to X" (an ambiguous success-shaped
       failure).

This file pins the fixes: real line numbers, an explicit truncation marker,
and an edit() that either applies for real or fails loudly with the near-match.
"""

from __future__ import annotations

from pathlib import Path

from hcode_v2.tools.files import _MAX_READ_LINES, edit, read


def _numbered_line(result: str, n: int) -> str:
    """Return the numbered-output line whose label is exactly `n:` (stripped)."""
    for line in result.splitlines():
        # format is "{width:4}: content" — compare the label portion before ':'.
        label = line.split(":", 1)[0].strip()
        if label == str(n):
            return line
    raise AssertionError(f"no line labeled {n} in:\n{result}")


# ── C2: ranged reads must number from the REAL file position ──────────────────

def test_read_range_labels_real_line_numbers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "big.py"
    target.write_text("".join(f"line{i}\n" for i in range(1, 151)), encoding="utf-8")  # 150 lines

    result = read.func(path=str(target), start_line=100, end_line=120)

    # Must be labeled 100..120 (NOT 1..21 — the pre-fix bug).
    assert _numbered_line(result, 100).endswith("line100")
    assert _numbered_line(result, 120).endswith("line120")
    # The old mislabeled line "1:" must not appear as a label in this output.
    assert not any(line.split(":", 1)[0].strip() == "1" for line in result.splitlines())
    # Exactly 21 lines returned (120 - 100 + 1).
    assert len(result.splitlines()) == 21


def test_read_range_with_only_start_line_labels_from_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "big.py"
    target.write_text("".join(f"line{i}\n" for i in range(1, 51)), encoding="utf-8")  # 50 lines

    result = read.func(path=str(target), start_line=40, end_line=None)

    assert _numbered_line(result, 40).endswith("line40")
    assert _numbered_line(result, 50).endswith("line50")


def test_read_no_range_small_file_labels_from_one(tmp_path: Path, monkeypatch) -> None:
    # Regression guard: an ordinary whole-file read (no range) is unaffected.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "small.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = read.func(path=str(target))

    assert _numbered_line(result, 1).endswith("alpha")
    assert _numbered_line(result, 3).endswith("gamma")


# ── C3: silent truncation must carry an explicit, honest marker ───────────────

def test_read_truncates_long_file_with_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "huge.py"
    total = _MAX_READ_LINES + 200  # comfortably past the cap
    target.write_text("".join(f"line{i}\n" for i in range(1, total + 1)), encoding="utf-8")

    result = read.func(path=str(target))

    # The real total must be stated, not hidden.
    assert f"of {total}" in result
    assert "truncated" in result
    assert "start_line" in result and "end_line" in result  # tells the model how to see more
    # Only the first _MAX_READ_LINES lines are shown.
    assert _numbered_line(result, _MAX_READ_LINES).endswith(f"line{_MAX_READ_LINES}")
    assert not any(
        line.split(":", 1)[0].strip() == str(_MAX_READ_LINES + 1) for line in result.splitlines()
    )


def test_read_under_cap_has_no_truncation_marker(tmp_path: Path, monkeypatch) -> None:
    # Regression guard: files at/under the cap are unaffected — no marker noise.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "normal.py"
    target.write_text("".join(f"line{i}\n" for i in range(1, 101)), encoding="utf-8")  # 100 lines

    result = read.func(path=str(target))

    assert "truncated" not in result


def test_read_explicit_range_of_large_file_is_not_truncated(tmp_path: Path, monkeypatch) -> None:
    # An explicit range is an intentional slice, not the silent whole-file cutoff
    # — it must never carry the truncation marker even on a file past the cap.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "huge.py"
    total = _MAX_READ_LINES + 200
    target.write_text("".join(f"line{i}\n" for i in range(1, total + 1)), encoding="utf-8")

    result = read.func(path=str(target), start_line=total - 10, end_line=total)

    assert "truncated" not in result
    assert _numbered_line(result, total).endswith(f"line{total}")


# ── T2: edit() — exact match unchanged, whitespace mismatch applies for real,
#       near-miss reports the closest region, unrelated miss fails gracefully ──

def test_edit_exact_match_still_applies_unchanged(tmp_path: Path, monkeypatch) -> None:
    # Regression guard: the exact-match path (the common case) is byte-identical
    # to before this fix.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "calc.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    content, artifact = edit.func(
        path=str(target), old_string="return a + b", new_string="return a - b"
    )

    assert "Edited calc.py" in content
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert artifact["additions"] == 1 and artifact["deletions"] == 1


def test_edit_indentation_mismatch_applies_instead_of_silent_no_op(tmp_path: Path, monkeypatch) -> None:
    # THE bug: old_string's internal lines have different indentation than the
    # file's real content. Pre-fix, the broken fuzzy path passed the (per-line
    # stripped) membership check, then the replace silently no-op'd, returning
    # "No changes to X" while the file stayed untouched. Post-fix it must
    # actually apply the edit.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "svc.py"
    target.write_text(
        "class Svc:\n"
        "    def run(self):\n"
        "        result = compute()\n"
        "        return result\n",
        encoding="utf-8",
    )
    # Same textual content as the real block, but 2-space indent instead of 8.
    old_mismatched = "  result = compute()\n  return result"
    new_string = "        result = compute()\n        return result * 2"

    content, artifact = edit.func(path=str(target), old_string=old_mismatched, new_string=new_string)

    assert "No changes" not in content, f"silent no-op regression: {content!r}"
    assert "Edited svc.py" in content
    on_disk = target.read_text(encoding="utf-8")
    assert "return result * 2" in on_disk, "the whitespace-mismatched edit did not apply"
    assert artifact["diff"], "artifact must carry a real diff, not an empty one"


def test_edit_near_miss_reports_closest_region_and_leaves_file_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "svc.py"
    original_text = (
        "def compute_total(items):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item.price\n"
        "    return total\n"
    )
    target.write_text(original_text, encoding="utf-8")
    # Close but not equal even after whitespace-normalizing (wrong variable name).
    near_miss = "    for entry in items:\n        total += entry.price"

    content, artifact = edit.func(path=str(target), old_string=near_miss, new_string="pass")

    assert "Closest match" in content
    assert "line" in content  # cites a location
    assert target.read_text(encoding="utf-8") == original_text, "a failed edit must never touch the file"
    assert artifact["diff"] == ""


def test_edit_unrelated_miss_fails_gracefully_with_no_similar_region(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "svc.py"
    original_text = "def compute_total(items):\n    return sum(i.price for i in items)\n"
    target.write_text(original_text, encoding="utf-8")

    content, artifact = edit.func(
        path=str(target),
        old_string="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)",
        new_string="whatever",
    )

    assert "Error: old_string not found" in content
    assert target.read_text(encoding="utf-8") == original_text
    assert artifact["diff"] == ""


def test_edit_true_no_op_still_reports_no_changes(tmp_path: Path, monkeypatch) -> None:
    # A GENUINE no-op (new_string identical to old_string) is a different case
    # from the silent-no-op bug — it should still report "No changes", since
    # nothing was actually asked to change.
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    target = tmp_path / "svc.py"
    target.write_text("value = 1\n", encoding="utf-8")

    content, _ = edit.func(path=str(target), old_string="value = 1", new_string="value = 1")

    assert "No changes" in content
