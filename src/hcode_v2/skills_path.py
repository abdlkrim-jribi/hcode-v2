"""Resolve HCode skill directories independently of the process CWD.

The built-in skills ship with the install at the package/repo root's
``.hcode/skills`` directory.  Resolving them relative to ``__file__`` (an
*install-relative* anchor) — rather than the literal ``Path(".hcode/skills")``,
which resolves against the process CWD — is what lets ``hcode skill``, the chat
``/skills`` command and the daemon's ``list_skills`` surface the built-in skills
from *any* working directory.  This matters most for the desktop daemon, which
``os.chdir``s into the user's picked ``work_dir`` before serving requests: a
CWD-relative lookup then resolves under the user's project and finds nothing.

The model is "both":

* **built-in** (ALWAYS): install-relative, CWD-independent.
* **project-local** (OPTIONAL): ``<work_dir>/.hcode/skills``, merged in only
  when it exists — so a user's per-project skills appear alongside the built-ins.

Only the *default* is computed here.  An explicit ``--dir`` / ``--skills-dir``
override still wins at every call site (it is passed straight through and never
routed through this module).
"""

from __future__ import annotations

from pathlib import Path

# ``.hcode/skills`` as a relative fragment, reused for every candidate root.
_SKILLS_SUBPATH = Path(".hcode") / "skills"
_SKILL_FILENAME = "SKILL.md"


def builtin_skills_dir() -> Path:
    """Return the install-relative directory holding the built-in skills.

    Anchored at ``__file__`` so the result never depends on the process CWD.
    This file lives at ``src/hcode_v2/skills_path.py``; ``parents[2]`` is the
    repo / package root, where the bundled ``.hcode/skills`` lives in a source
    checkout.  A packaged layout that ships the skills next to the package is
    also probed.  The first candidate that exists wins; if none do, the
    canonical source-layout path is returned unchanged (callers tolerate a
    missing directory and simply yield no built-in skills).
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / _SKILLS_SUBPATH,  # source checkout: <repo-root>/.hcode/skills
        here.parents[1] / _SKILLS_SUBPATH,  # packaged: <site-packages>/.hcode/skills
        here.parent / _SKILLS_SUBPATH,      # packaged: alongside the package itself
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def default_skills_dirs(work_dir: str | None = None) -> list[str]:
    """Return the default skill roots ("both" model), as absolute path strings.

    The built-in (install-relative) directory is always first.  A project-local
    ``<work_dir>/.hcode/skills`` is appended only when it exists and differs
    from the built-in directory.  ``work_dir`` defaults to the current directory
    *for the project-local probe only* — the built-in root never depends on CWD.
    """
    built_in = builtin_skills_dir().resolve()
    dirs: list[str] = [str(built_in)]

    base = Path(work_dir) if work_dir else Path.cwd()
    project = (base / _SKILLS_SUBPATH).resolve()
    if project != built_in and project.is_dir():
        dirs.append(str(project))

    return dirs


def list_skill_names(dirs: list[str]) -> list[str]:
    """Return the sorted, de-duplicated names of skills found across ``dirs``.

    A skill is an immediate sub-directory containing a ``SKILL.md`` file.  Names
    are unioned across every root (built-in + project-local), so a project-local
    skill appears alongside the built-ins; a duplicate name collapses to one.
    """
    names: set[str] = set()
    for d in dirs:
        root = Path(d)
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.is_dir() and (entry / _SKILL_FILENAME).exists():
                names.add(entry.name)
    return sorted(names)


__all__ = ["builtin_skills_dir", "default_skills_dirs", "list_skill_names"]
