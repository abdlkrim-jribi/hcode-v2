"""SelectiveSkillsMiddleware — allowlist-based skill filtering without vendored edits.

Subclasses the vendored ``HCodeSkillsMiddleware`` and overrides its private
``_load_skills_from_dir`` method to skip skills whose name is not in the
allowlist.  The ``{skill_dir}`` placeholder still resolves to the real on-disk
folder (no copying, no temp dirs).

Vendored-private dependency: ``_load_skills_from_dir`` is a private method of
``HCodeSkillsMiddleware`` (prefix ``_``, no double-underscore mangling).  This is
an intentional coupling, following the same pattern as the ``_ToolExclusionMiddleware``
import in ``factory.py`` — both are documented with a re-vendor caveat:
re-check the method name when ``libs/deepagents/`` is updated.
"""

from __future__ import annotations

from typing import Any

from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware


class SelectiveSkillsMiddleware(HCodeSkillsMiddleware):
    """Middleware that loads only the skills named in *allow*.

    Args:
        skills_dir: Path to the skills root directory (forwarded to the parent).
        allow: Frozenset of skill-directory names to keep.  Names NOT in this
            set are silently skipped.  An empty frozenset → no skills loaded.
    """

    def __init__(self, skills_dir: str, allow: frozenset[str]) -> None:
        super().__init__(skills_dir=skills_dir)
        self._allow = allow

    def _load_skills_from_dir(self) -> list[dict[str, Any]]:
        """Load all skills from the directory, then filter to the allowlist."""
        all_skills = super()._load_skills_from_dir()
        return [s for s in all_skills if s["name"] in self._allow]


__all__ = ["SelectiveSkillsMiddleware"]
