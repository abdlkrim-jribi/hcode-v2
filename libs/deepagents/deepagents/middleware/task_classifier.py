"""Task complexity classifier for PEV (Plan-Execute-Verify) middleware."""

from __future__ import annotations

from typing import Literal

TaskComplexity = Literal["trivial", "simple", "moderate", "complex"]

_TRIVIAL_PATTERNS: tuple[str, ...] = (
    "list ",
    "show ",
    "get ",
    "what is",
    "who is",
    "where is",
)

_COMPLEX_PATTERNS: tuple[str, ...] = (
    "implement",
    "refactor",
    "build",
    "create",
    "migrate",
    "architect",
    "design",
    "add feature",
    "fix bug",
    "debug",
    "optimize",
    "rewrite",
    "integrate",
    "deploy",
)

_LONG_TASK_WORD_THRESHOLD = 15
_SHORT_QUESTION_WORD_THRESHOLD = 10


class TaskClassifier:
    """Classifies task complexity using keyword-based heuristics.

    Categorises a user task into one of four complexity levels:

    - ``trivial`` — single lookup question, simple list/show/get command.
    - ``simple`` — short task with no complex engineering keywords.
    - ``moderate`` — longer task without strong complexity signals.
    - ``complex`` — contains engineering keywords (implement, refactor, …).

    Example:
        ```python
        classifier = TaskClassifier()
        phase = classifier.get_initial_phase("implement a REST API")
        # phase == "plan"
        ```
    """

    TRIVIAL_PATTERNS: tuple[str, ...] = _TRIVIAL_PATTERNS
    COMPLEX_PATTERNS: tuple[str, ...] = _COMPLEX_PATTERNS

    def classify(self, task: str) -> TaskComplexity:
        """Return the complexity level of ``task``.

        Args:
            task: The user's task description.

        Returns:
            One of ``"trivial"``, ``"simple"``, ``"moderate"``, or ``"complex"``.
        """
        lower = task.lower().strip()
        if self.is_trivial(task):
            return "trivial"
        if self._is_complex(lower):
            return "complex"
        if len(task.split()) > _LONG_TASK_WORD_THRESHOLD:
            return "moderate"
        return "simple"

    def is_trivial(self, task: str) -> bool:
        """Return ``True`` if the task is trivial (single question or simple lookup).

        Args:
            task: The user's task description.

        Returns:
            ``True`` when the task is a trivial lookup or single-answer question.
        """
        lower = task.lower().strip()
        # Single short question
        if lower.count("?") == 1 and len(task.split()) <= _SHORT_QUESTION_WORD_THRESHOLD:
            return True
        # Starts with a trivial command prefix
        return any(lower.startswith(pattern) for pattern in self.TRIVIAL_PATTERNS)

    def _is_complex(self, lower_task: str) -> bool:
        """Return ``True`` if the task contains complex engineering keywords."""
        return any(pattern in lower_task for pattern in self.COMPLEX_PATTERNS)

    def requires_pev(self, task: str) -> bool:
        """Return ``True`` only for complex tasks that benefit from Plan-Execute-Verify.

        Args:
            task: The user's task description.

        Returns:
            ``True`` when the task is classified as ``"complex"``.
        """
        return self.classify(task) == "complex"

    def get_initial_phase(self, task: str) -> str:
        """Return the initial PEV phase for the given task.

        Args:
            task: The user's task description.

        Returns:
            ``"trivial"`` for trivial tasks, ``"fast"`` for simple/moderate tasks,
            ``"plan"`` for complex tasks.
        """
        complexity = self.classify(task)
        if complexity == "trivial":
            return "trivial"
        if complexity == "complex":
            return "plan"
        return "fast"


__all__ = ["TaskClassifier", "TaskComplexity"]
