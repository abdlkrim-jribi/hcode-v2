"""Notebook tools: notebook_read, notebook_edit, notebook_execute."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from langchain_core.tools import StructuredTool, tool
from pydantic import BaseModel

from hcode_v2.tools.base import get_root_dir


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else get_root_dir() / p


@tool
def notebook_read(path: str) -> str:
    """Read a Jupyter notebook and return its cells as text.

    Args:
        path: Path to the .ipynb file.
    """
    fp = _resolve(path)
    if not fp.exists():
        return f"Error: notebook not found: {path}"
    try:
        nb = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Error reading notebook: {exc}"

    lines: list[str] = []
    for i, cell in enumerate(nb.get("cells", []), 1):
        cell_type = cell.get("cell_type", "unknown")
        source = "".join(cell.get("source", []))
        lines.append(f"[Cell {i} — {cell_type}]\n{source}")
        outputs = cell.get("outputs", [])
        if outputs:
            out_texts: list[str] = []
            for out in outputs:
                if "text" in out:
                    out_texts.append("".join(out["text"]))
                elif "data" in out and "text/plain" in out["data"]:
                    out_texts.append("".join(out["data"]["text/plain"]))
            if out_texts:
                lines.append("[Output]\n" + "\n".join(out_texts))
        lines.append("")
    return "\n".join(lines)


class _NotebookEditInput(BaseModel):
    path: str
    cell_index: int
    operation: str
    source: Optional[str] = None
    cell_type: str = "code"


def _notebook_edit_fn(
    path: str,
    cell_index: int,
    operation: str,
    source: Optional[str] = None,
    cell_type: str = "code",
) -> str:
    """Edit a Jupyter notebook cell.

    Args:
        path: Path to the .ipynb file.
        cell_index: 1-based cell index.
        operation: One of 'replace', 'insert', 'delete'.
        source: New cell source (required for 'replace' and 'insert').
        cell_type: Cell type for insert — 'code' or 'markdown'.
    """
    fp = _resolve(path)
    if not fp.exists():
        return f"Error: notebook not found: {path}"
    try:
        nb = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Error reading notebook: {exc}"

    cells = nb.get("cells", [])
    idx = cell_index - 1

    if operation == "replace":
        if idx < 0 or idx >= len(cells):
            return f"Error: cell index {cell_index} out of range (1..{len(cells)})"
        if source is None:
            return "Error: source required for replace"
        cells[idx]["source"] = source
        cells[idx]["outputs"] = []
    elif operation == "insert":
        if source is None:
            return "Error: source required for insert"
        new_cell: dict = {
            "cell_type": cell_type,
            "source": source,
            "metadata": {},
            "outputs": [] if cell_type == "code" else None,
        }
        if new_cell["outputs"] is None:
            del new_cell["outputs"]
        if cell_type == "code":
            new_cell["execution_count"] = None
        cells.insert(max(0, min(idx, len(cells))), new_cell)
    elif operation == "delete":
        if idx < 0 or idx >= len(cells):
            return f"Error: cell index {cell_index} out of range (1..{len(cells)})"
        cells.pop(idx)
    else:
        return f"Error: unknown operation '{operation}'. Use replace, insert, or delete."

    nb["cells"] = cells
    try:
        fp.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    except Exception as exc:
        return f"Error writing notebook: {exc}"
    return f"Notebook {operation} on cell {cell_index} in {path}."


notebook_edit = StructuredTool.from_function(
    func=_notebook_edit_fn,
    name="notebook_edit",
    description="Edit a Jupyter notebook cell (replace, insert, delete).",
    args_schema=_NotebookEditInput,
)


@tool
def notebook_execute(path: str, timeout: int = 60) -> str:
    """Execute a Jupyter notebook in place using nbconvert.

    Args:
        path: Path to the .ipynb file.
        timeout: Seconds per cell before timeout.
    """
    fp = _resolve(path)
    if not fp.exists():
        return f"Error: notebook not found: {path}"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / fp.name
        result = subprocess.run(
            [
                "jupyter", "nbconvert",
                "--to", "notebook",
                "--execute",
                f"--ExecutePreprocessor.timeout={timeout}",
                "--output", str(out_path),
                str(fp),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout * 10,
        )
        if result.returncode != 0:
            return f"Execution failed:\n{result.stderr or result.stdout}"

        try:
            nb = json.loads(out_path.read_text(encoding="utf-8"))
            fp.write_text(json.dumps(nb, indent=1), encoding="utf-8")
        except Exception as exc:
            return f"Notebook executed but could not save result: {exc}"

    return f"Notebook executed successfully: {path}"
