"""HCode skills middleware — loads SKILL.md files from the .hcode/skills/ directory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr

from deepagents.middleware._utils import append_to_system_message

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.runtime import Runtime

_SKILL_FILENAME: str = "SKILL.md"
_SKILL_DIR_PLACEHOLDER: str = "{skill_dir}"


class HCodeSkillsState(AgentState):
    """Agent state extended with HCode skills discovery fields."""

    _hcode_skills: Annotated[NotRequired[list[dict[str, Any]]], PrivateStateAttr]
    """List of loaded HCode skill descriptors (name, path, content)."""

    _hcode_skills_loaded: Annotated[NotRequired[bool], PrivateStateAttr]
    """``True`` after the initial skill scan has run."""


class HCodeSkillsMiddleware(AgentMiddleware):
    """Middleware that discovers skills from an HCode ``.hcode/skills/`` directory layout.

    Each immediate sub-directory of ``skills_dir`` that contains a ``SKILL.md``
    file is registered as a skill.  The placeholder ``{skill_dir}`` inside
    ``SKILL.md`` is replaced with the absolute path of the containing folder so
    that skill authors can reference sibling files with relative syntax.

    The directory is scanned once per agent session (``before_agent``).  If the
    directory does not exist the middleware silently returns an empty skill list —
    it does not raise.

    Example layout::

        .hcode/skills/
            code-review/
                SKILL.md
            testing/
                SKILL.md
                run_tests.sh

    Args:
        skills_dir: Path to the skills root directory.
            Defaults to ``.hcode/skills``.
    """

    state_schema = HCodeSkillsState

    def __init__(self, skills_dir: str = ".hcode/skills") -> None:
        """Initialise with the path to the HCode skills directory.

        Args:
            skills_dir: Local path to the directory that contains skill sub-folders.
        """
        self.skills_dir: Path = Path(skills_dir)

    def _load_skills_from_dir(self) -> list[dict[str, Any]]:
        """Scan ``self.skills_dir`` and return a list of skill descriptor dicts.

        Returns:
            List of ``{"name": str, "path": str, "content": str}`` dicts, one
            per sub-directory that contains a ``SKILL.md`` file.  Empty if the
            root directory does not exist.
        """
        if not self.skills_dir.is_dir():
            return []

        skills: list[dict[str, Any]] = []
        for entry in sorted(self.skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / _SKILL_FILENAME
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text()
                content = content.replace(_SKILL_DIR_PLACEHOLDER, str(entry))
                skills.append({
                    "name": entry.name,
                    "path": str(skill_md),
                    "content": content,
                })
            except OSError:
                continue

        return skills

    def _build_skills_section(self, skills: list[dict[str, Any]]) -> str:
        """Build a system-prompt fragment listing all available HCode skills.

        Args:
            skills: List of skill descriptor dicts.

        Returns:
            Formatted Markdown string for system-prompt injection.
        """
        lines: list[str] = ["## HCode Skills\n"]
        for skill in skills:
            lines.append(f"### {skill['name']}\n{skill['content']}\n")
        return "\n".join(lines)

    def before_agent(self, state: HCodeSkillsState, runtime: Runtime) -> dict[str, Any] | None:
        """Scan the HCode skills directory and load skill metadata into state.

        Skipped when the state already contains ``_hcode_skills_loaded = True``
        (checkpointed session resumption).

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with the loaded skills list, or ``None`` if already loaded.
        """
        if state.get("_hcode_skills_loaded"):
            return None

        skills = self._load_skills_from_dir()
        return {
            "_hcode_skills": skills,
            "_hcode_skills_loaded": True,
        }

    async def abefore_agent(self, state: HCodeSkillsState, runtime: Runtime) -> dict[str, Any] | None:
        """Delegate asynchronously to :meth:`before_agent`.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with the loaded skills list, or ``None`` if already loaded.
        """
        return self.before_agent(state, runtime)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject a skills summary into the system prompt before each model call.

        Args:
            request: Model request from the agent loop.
            handler: Next handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        skills: list[dict[str, Any]] = request.state.get("_hcode_skills", [])
        if not skills:
            return handler(request)
        new_sm = append_to_system_message(request.system_message, self._build_skills_section(skills))
        return handler(request.override(system_message=new_sm))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Inject a skills summary into the system prompt before each async model call.

        Args:
            request: Model request from the agent loop.
            handler: Next async handler in the middleware chain.

        Returns:
            Model response from the handler.
        """
        skills: list[dict[str, Any]] = request.state.get("_hcode_skills", [])
        if not skills:
            return await handler(request)
        new_sm = append_to_system_message(request.system_message, self._build_skills_section(skills))
        return await handler(request.override(system_message=new_sm))


__all__ = ["HCodeSkillsMiddleware", "HCodeSkillsState"]
