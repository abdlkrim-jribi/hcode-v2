"""Project-map builder — a compact orientation block so the model plans with sight.

The audit's #1 finding: every task started with only the ``<env>`` block (cwd,
platform, git flag, date). The PLAN phase (pev.py) demands "name the exact
file(s) each step touches" — but the model could see NO files, so it fabricated
plausible names, and the execute phase then burned its iteration budget
re-discovering what the plan should have known.

``build_project_map(work_dir)`` returns a ``<project_map>`` block giving the
model the project's SHAPE, not its contents:

  * project name / type / entry points + direct dependencies (from the manifest)
  * a depth-limited, denylist-pruned, entry-capped file TREE (real paths)
  * the README's first lines

Design judgment — ORIENT, don't dump. Everything is bounded:
  * ``_MAX_DEPTH`` levels, ``_ENTRY_BUDGET`` total tree lines, ``_MAX_CHILDREN``
    per directory (overflow collapses to "... (+N more)"), directories at the
    depth limit collapse to a "(N files)" count.
  * The whole block is hard-capped at ``_MAX_CHARS`` (~1500 tokens); the tree is
    trimmed first (most compressible), then the README.
  * A pathologically large or unreadable tree degrades to top-level dirs with
    counts rather than a 10k-token wall.

Gitignore-awareness is best-effort: a strong hardcoded denylist of build/cache/
VCS/dependency directories, PLUS bare directory names read from the top-level
``.gitignore``. It is NOT a full gitignore engine (no negations, nested files,
or glob semantics) — the goal is "don't show junk", not perfect fidelity.

HCode-side, standalone, never raises: any failure returns ``""`` so the agent
falls back to exactly the pre-existing (map-less) behaviour — zero regression.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Bounds (the signal/noise knobs) ───────────────────────────────────────────
_MAX_DEPTH = 3            # src-layout puts real code at depth 3 (src/pkg/module/)
_ENTRY_BUDGET = 220       # total tree lines before truncation (char-cap is backstop)
_MAX_CHILDREN = 25        # per-directory entries before "... (+N more)"
_MAX_TOP_DIRS = 30        # top-level dirs shown before "... (+N more)"
_MIN_DIR_BUDGET = 6       # each top-level dir gets AT LEAST this many deep lines,
#                           so no single big dir (examples/) starves src/ & tests/
_MAX_CHARS = 6000         # whole-block hard cap (~1500 tokens @ ~4 chars/token)
_README_MAX_LINES = 25
_README_MAX_CHARS = 800
_MAX_DEPS_SHOWN = 25
_FILE_COUNT_CEILING = 999  # stop counting a collapsed dir's files past this

# Directories never worth showing: VCS, virtualenvs, caches, build output, deps,
# editor/agent runtime state. The goal is to spend the tree budget on real code.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".bzr",
    "node_modules", "bower_components", "vendor", "Pods",
    ".venv", "venv", "env", "virtualenv", ".virtualenv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".cache", ".gradle", ".terraform", ".serverless",
    "build", "dist", "out", "target", ".next", ".nuxt", ".svelte-kit",
    "coverage", "htmlcov", ".nyc_output", ".vite", "gen",
    ".idea", ".vscode", ".vscode-test", ".DS_Store",
    ".hcode", ".claude",  # HCode/agent runtime state — sessions, chat, not code
})
# Directory-name suffixes to skip (e.g. ``hcode_v2.egg-info``).
_SKIP_DIR_SUFFIXES = (".egg-info",)

# Binary / generated / lock files are not plannable code — drop them from the
# tree so the budget shows source & config, not screenshots and lockfiles.
_SKIP_FILE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".db", ".sqlite", ".sqlite3", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".lock", ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".mov", ".pdf",
    ".tsbuildinfo", ".map", ".class", ".o", ".a",
})
_SKIP_FILE_NAMES = frozenset({
    "package-lock.json", "uv.lock", "poetry.lock", "Cargo.lock",
    "yarn.lock", "pnpm-lock.yaml", ".DS_Store",
})

_MANIFEST_FILES = ("pyproject.toml", "package.json", "requirements.txt", "Cargo.toml", "go.mod")
_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")


def build_project_map(work_dir: str, *, open_file: Optional[str] = None) -> str:
    """Return a ``<project_map>`` orientation block for *work_dir*, or ``""``.

    ``open_file`` (currently unused by callers) is the seam for a follow-up that
    plumbs the GUI's open-file path through so the map can note "currently open".
    Passing it today adds a single line; the daemon/UI wiring is deferred.

    Never raises — any error (unreadable dir, odd manifest) yields ``""`` so the
    agent's prompt is exactly what it was before this feature.
    """
    try:
        root = Path(work_dir).resolve()
        if not root.is_dir():
            return ""

        skip = set(_SKIP_DIRS) | _read_gitignore_dirs(root)
        header = _build_header(root)
        tree = _build_tree_section(root, skip)
        readme = _build_readme_section(root)

        sections = [s for s in (header, tree, readme) if s]
        if not sections:
            return ""

        open_note = ""
        if open_file:
            open_note = f"\nCurrently open in the editor: {open_file}\n"

        body = "\n".join(sections)
        block = (
            "<project_map>\n"
            "The real structure of this project. Reference these EXACT paths when "
            "you plan — do not invent file names.\n\n"
            f"{body}\n"
            f"{open_note}"
            "</project_map>"
        )
        return _enforce_char_cap(block, header, tree, readme, open_note)
    except Exception as exc:  # noqa: BLE001 — orientation must never break agent build
        logger.debug("project map build failed for %s: %s", work_dir, exc)
        return ""


# ── .gitignore (best-effort directory names only) ─────────────────────────────

def _read_gitignore_dirs(root: Path) -> set[str]:
    """Bare directory names from the top-level .gitignore (best-effort).

    Only simple entries are honored: a bare ``name`` or ``name/`` with no path
    separator, no glob character, and not a negation. Everything else (globs,
    nested paths, ``!`` un-ignores) is skipped — this is orientation hygiene,
    not a gitignore engine.
    """
    gi = root / ".gitignore"
    if not gi.is_file():
        return set()
    out: set[str] = set()
    try:
        for raw in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            name = line.rstrip("/")
            if not name or "/" in name or any(c in name for c in "*?[]"):
                continue
            out.add(name)
    except OSError:
        pass
    return out


def _is_skipped_dir(name: str, skip: set[str]) -> bool:
    return name in skip or name.startswith(".git") or name.endswith(_SKIP_DIR_SUFFIXES)


def _is_skipped_file(name: str) -> bool:
    if name in _SKIP_FILE_NAMES:
        return True
    ext = ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""
    return ext in _SKIP_FILE_EXTS


# ── Manifest / header ─────────────────────────────────────────────────────────

def _build_header(root: Path) -> str:
    """Project name + type + entry points + direct deps from the first manifest."""
    name, ptype, entry_points, deps, description = _parse_manifest(root)
    if not (name or deps or ptype):
        return ""
    lines: list[str] = []
    title = name or root.name
    type_suffix = f" ({ptype})" if ptype else ""
    desc_suffix = f" — {description}" if description else ""
    lines.append(f"Project: {title}{type_suffix}{desc_suffix}")
    if entry_points:
        lines.append("Entry points: " + "; ".join(entry_points[:6]))
    if deps:
        shown = deps[:_MAX_DEPS_SHOWN]
        more = f" (+{len(deps) - len(shown)} more)" if len(deps) > len(shown) else ""
        lines.append("Direct dependencies: " + ", ".join(shown) + more)
    return "\n".join(lines)


def _parse_manifest(root: Path):
    """Return (name, project_type, entry_points, deps, description) from the first
    recognized manifest at *root*; blanks/empties when none or on parse error."""
    for fname in _MANIFEST_FILES:
        p = root / fname
        if not p.is_file():
            continue
        try:
            if fname == "pyproject.toml":
                return _parse_pyproject(p)
            if fname == "package.json":
                return _parse_package_json(p)
            if fname == "requirements.txt":
                return _parse_requirements(root, p)
            if fname == "Cargo.toml":
                return _parse_cargo(p)
            if fname == "go.mod":
                return _parse_go_mod(p)
        except Exception as exc:  # noqa: BLE001 — a bad manifest just yields no header
            logger.debug("manifest parse failed (%s): %s", fname, exc)
            return "", "", [], [], ""
    return "", "", [], [], ""


def _dep_name(spec: str) -> str:
    """Strip version constraints/extras from a dependency spec → the bare name."""
    for sep in ("[", "==", ">=", "<=", "~=", "!=", ">", "<", " ", ";", "@"):
        idx = spec.find(sep)
        if idx > 0:
            spec = spec[:idx]
    return spec.strip()


def _parse_pyproject(p: Path):
    import tomllib
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    project = data.get("project", {})
    # PEP 621 first, then poetry.
    if project:
        name = project.get("name", "")
        description = project.get("description", "")
        deps = [_dep_name(d) for d in project.get("dependencies", []) if isinstance(d, str)]
        scripts = project.get("scripts", {})
        entry_points = [f"{k} = {v}" for k, v in scripts.items()] if isinstance(scripts, dict) else []
    else:
        poetry = data.get("tool", {}).get("poetry", {})
        name = poetry.get("name", "")
        description = poetry.get("description", "")
        deps = [k for k in poetry.get("dependencies", {}) if k.lower() != "python"]
        entry_points = [f"{k} = {v}" for k, v in poetry.get("scripts", {}).items()]
    return name, "Python", entry_points, [d for d in deps if d], description


def _parse_package_json(p: Path):
    data = json.loads(p.read_text(encoding="utf-8"))
    name = data.get("name", "") if isinstance(data, dict) else ""
    description = data.get("description", "") if isinstance(data, dict) else ""
    deps = list((data.get("dependencies") or {}).keys()) if isinstance(data, dict) else []
    scripts = data.get("scripts") or {} if isinstance(data, dict) else {}
    # A couple of the most telling scripts as "entry points".
    entry_points = [f"{k}: {scripts[k]}" for k in ("dev", "start", "build") if k in scripts]
    return name, "Node/JS", entry_points, deps, description


def _parse_requirements(root: Path, p: Path):
    deps: list[str] = []
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "-")):
            n = _dep_name(line)
            if n:
                deps.append(n)
    return root.name, "Python", [], deps, ""


def _parse_cargo(p: Path):
    import tomllib
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    pkg = data.get("package", {})
    name = pkg.get("name", "")
    description = pkg.get("description", "")
    deps = list(data.get("dependencies", {}).keys())
    return name, "Rust", [], deps, description


def _parse_go_mod(p: Path):
    name = ""
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("module "):
            name = line[len("module "):].strip()
            break
    return name, "Go", [], [], ""


# ── File tree ─────────────────────────────────────────────────────────────────

def _count_files(dir_path: Path, skip: set[str]) -> int:
    """Bounded recursive file count for a collapsed directory (stops at ceiling)."""
    total = 0
    stack = [dir_path]
    while stack and total < _FILE_COUNT_CEILING:
        try:
            for entry in stack.pop().iterdir():
                if entry.is_dir():
                    if not _is_skipped_dir(entry.name, skip):
                        stack.append(entry)
                elif entry.is_file():
                    total += 1
                    if total >= _FILE_COUNT_CEILING:
                        break
        except OSError:
            continue
    return total


def _listing(dir_path: Path, skip: set[str]) -> tuple[list[Path], list[Path]]:
    """(child dirs, child files) of *dir_path*, filtered + sorted; ([],[]) on error."""
    try:
        entries = list(dir_path.iterdir())
    except OSError:
        return [], []
    dirs = sorted(
        (e for e in entries if e.is_dir() and not _is_skipped_dir(e.name, skip)),
        key=lambda p: p.name.lower(),
    )
    files = sorted(
        (e for e in entries if e.is_file() and not _is_skipped_file(e.name)),
        key=lambda p: p.name.lower(),
    )
    return dirs, files


def _compress_chain(dir_path: Path, name: str, skip: set[str]) -> tuple[Path, str]:
    """Collapse a single-child directory chain into one label.

    A src-layout puts real modules at depth 4 (``src/pkg/subpkg/module.py``). When
    a directory holds exactly ONE sub-directory and no files (``src/`` → only
    ``hcode_v2/``), rendering them as one ``src/hcode_v2/`` node reclaims a depth
    level so the meaningful children (and their files) fall within the depth cap.
    Returns the deepest directory reached and the combined ``a/b/c`` label."""
    seen = {dir_path.resolve()}
    for _ in range(6):  # bound the collapse so a symlink cycle can't loop forever
        dirs, files = _listing(dir_path, skip)
        if len(dirs) == 1 and not files:
            child = dirs[0]
            rp = child.resolve()
            if rp in seen:
                break
            seen.add(rp)
            dir_path, name = child, f"{name}/{child.name}"
        else:
            break
    return dir_path, name


def _expand(dir_path: Path, depth: int, budget: list[int], skip: set[str], lines: list[str]) -> None:
    """Depth-first render of *dir_path*'s subtree into *lines*, spending *budget*.

    Directories at the depth limit collapse to a "(N files)" count; single-child
    chains are path-compressed (see _compress_chain); per-directory breadth is
    capped at _MAX_CHILDREN. *budget* is a shared single-element list so nested
    calls draw from the same allowance."""
    if budget[0] <= 0 or depth >= _MAX_DEPTH:
        return
    dirs, files = _listing(dir_path, skip)
    indent = "  " * (depth + 1)

    for d in dirs[:_MAX_CHILDREN]:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        deepest, label = _compress_chain(d, d.name, skip)
        if depth + 1 >= _MAX_DEPTH:
            n = _count_files(deepest, skip)
            suffix = f" ({n}{'+' if n >= _FILE_COUNT_CEILING else ''} files)" if n else ""
            lines.append(f"{indent}{label}/{suffix}")
        else:
            lines.append(f"{indent}{label}/")
            _expand(deepest, depth + 1, budget, skip, lines)
    if len(dirs) > _MAX_CHILDREN:
        lines.append(f"{indent}... (+{len(dirs) - _MAX_CHILDREN} more dirs)")

    for f in files[:_MAX_CHILDREN]:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        lines.append(f"{indent}{f.name}")
    if len(files) > _MAX_CHILDREN:
        lines.append(f"{indent}... (+{len(files) - _MAX_CHILDREN} more files)")


def _build_tree_section(root: Path, skip: set[str]) -> str:
    """Fair-share tree: every top-level dir is shown and gets its OWN deep budget,
    so a big directory (examples/) can't starve src/ & tests/ of representation —
    the failure mode of a single alphabetical budget."""
    top_dirs, top_files = _listing(root, skip)
    if not top_dirs and not top_files:
        return ""

    shown_dirs = top_dirs[:_MAX_TOP_DIRS]
    # Split the deep budget fairly across the shown top-level dirs, with a floor
    # so each still gets a useful glimpse even when there are many.
    per_dir = max(_MIN_DIR_BUDGET, _ENTRY_BUDGET // max(1, len(shown_dirs)))
    truncated = False
    lines: list[str] = []

    for d in shown_dirs:
        deepest, label = _compress_chain(d, d.name, skip)
        lines.append(f"  {label}/")
        budget = [per_dir]
        _expand(deepest, 1, budget, skip, lines)
        if budget[0] <= 0:
            truncated = True
    if len(top_dirs) > _MAX_TOP_DIRS:
        lines.append(f"  ... (+{len(top_dirs) - _MAX_TOP_DIRS} more dirs)")
        truncated = True

    for f in top_files[:_MAX_CHILDREN]:
        lines.append(f"  {f.name}")
    if len(top_files) > _MAX_CHILDREN:
        lines.append(f"  ... (+{len(top_files) - _MAX_CHILDREN} more files)")

    note = "\n  … (some deep folders collapsed to fit)" if truncated else ""
    return "Structure:\n" + "\n".join(lines) + note


# ── README ────────────────────────────────────────────────────────────────────

def _build_readme_section(root: Path) -> str:
    for name in _README_NAMES:
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = text.splitlines()[:_README_MAX_LINES]
        excerpt = "\n".join(lines).strip()
        if len(excerpt) > _README_MAX_CHARS:
            excerpt = excerpt[:_README_MAX_CHARS].rstrip() + " …"
        if not excerpt:
            return ""
        # Indent so the excerpt reads as a nested quote, not agent instructions.
        indented = "\n".join("  " + ln for ln in excerpt.splitlines())
        return f"README ({name}) excerpt:\n{indented}"
    return ""


# ── Whole-block size enforcement ──────────────────────────────────────────────

def _enforce_char_cap(block: str, header: str, tree: str, readme: str, open_note: str) -> str:
    """Keep the block under _MAX_CHARS, trimming the most compressible parts first
    (tree, then README) while always preserving the header + wrapper."""
    if len(block) <= _MAX_CHARS:
        return block

    # 1) Drop the README entirely.
    if readme:
        return _enforce_char_cap(
            _assemble(header, tree, "", open_note), header, tree, "", open_note
        )
    # 2) Hard-truncate the tree to fit.
    if tree:
        wrapper_len = len(_assemble(header, "", "", open_note))
        room = max(0, _MAX_CHARS - wrapper_len - 40)
        trimmed_tree = tree[:room].rstrip() + "\n  … (map truncated to fit)"
        return _assemble(header, trimmed_tree, "", open_note)
    # 3) Header alone still too big (pathological) — truncate the whole thing.
    return block[: _MAX_CHARS - 20].rstrip() + "\n…\n</project_map>"


def _assemble(header: str, tree: str, readme: str, open_note: str) -> str:
    body = "\n".join(s for s in (header, tree, readme) if s)
    return (
        "<project_map>\n"
        "The real structure of this project. Reference these EXACT paths when "
        "you plan — do not invent file names.\n\n"
        f"{body}\n"
        f"{open_note}"
        "</project_map>"
    )


__all__ = ["build_project_map"]
