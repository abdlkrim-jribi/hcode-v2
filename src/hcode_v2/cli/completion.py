"""prompt_toolkit input layer for ``hcode chat``.

Provides the interactive prompt used by the chat loop: slash-command
completion, file-path completion for path-like tokens and ``@file``
references, a small curated phrase completer, and persistent history with
history-based auto-suggest.

This module is the INPUT layer only — it never dispatches commands. The chat
loop in ``hcode_v2.cli.main`` keeps full ownership of what each command does;
completion is purely additive.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, merge_completers
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory

_DEFAULT_HISTORY_PATH = ".hcode/chat_history.txt"

# Only the commands the chat loop actually dispatches today (see the `chat`
# command in main.py). New commands must be wired there BEFORE being added
# here — no dead entries.
CHAT_COMMANDS: dict[str, str] = {
    "/exit": "End the session",
    "/quit": "End the session",
    "/skills": "List available skills",
    "/workflows": "List available workflows",
}

# Small curated starter phrases — intentionally short, not a catalog.
PHRASES: tuple[str, ...] = (
    "add tests to",
    "explain this code",
    "fix the bug in",
    "fix the failing test",
    "refactor this code to",
    "write a test for",
)

# Directories never offered by file completion (same conventions as v1).
_IGNORED_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
)

_FILE_CACHE_TTL_SECONDS = 30.0
_MAX_SCANNED_FILES = 500
_MAX_SCAN_DEPTH = 4


class SlashCommandCompleter(Completer):
    """Complete chat slash commands while the first word is being typed.

    Args:
        commands: ``{command: description}`` mapping. Defaults to the commands
            the chat loop handles (:data:`CHAT_COMMANDS`).
    """

    def __init__(self, commands: dict[str, str] | None = None) -> None:
        self.commands: dict[str, str] = commands or CHAT_COMMANDS

    def get_completions(
        self, document: Document, complete_event: object
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        # Only the leading word of the input can be a command.
        if not text.startswith("/") or " " in text:
            return
        for command, description in self.commands.items():
            if command.startswith(text):
                yield Completion(
                    command,
                    start_position=-len(text),
                    display_meta=description,
                )


class FilePathCompleter(Completer):
    """Complete path-like tokens and ``@file`` references against the work dir.

    A token triggers completion when it starts with ``@`` or contains a path
    separator. Matching is a case-insensitive prefix match on paths relative
    to ``root_dir``. The file list is scanned lazily and cached for
    :data:`_FILE_CACHE_TTL_SECONDS`.

    Args:
        root_dir: Directory whose files are offered. Defaults to the current
            working directory.
    """

    def __init__(self, root_dir: str | None = None) -> None:
        self.root_dir: Path = Path(root_dir or os.getcwd())
        self._cache: list[str] = []
        self._cache_time: float = 0.0

    def _scan_files(self) -> list[str]:
        """Walk ``root_dir`` and return relative POSIX paths, capped and filtered."""
        files: list[str] = []

        def scan(path: Path, depth: int) -> None:
            if depth > _MAX_SCAN_DEPTH or len(files) >= _MAX_SCANNED_FILES:
                return
            try:
                entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
            except (PermissionError, OSError):
                return
            for entry in entries:
                if len(files) >= _MAX_SCANNED_FILES:
                    return
                if entry.name.startswith(".") and entry.name not in (".env", ".gitignore"):
                    continue
                if entry.is_dir():
                    if entry.name not in _IGNORED_DIRS:
                        scan(entry, depth + 1)
                else:
                    files.append(entry.relative_to(self.root_dir).as_posix())

        scan(self.root_dir, 0)
        return files

    def _files(self) -> list[str]:
        now = time.monotonic()
        if now - self._cache_time > _FILE_CACHE_TTL_SECONDS:
            self._cache = self._scan_files()
            self._cache_time = now
        return self._cache

    def get_completions(
        self, document: Document, complete_event: object
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.endswith(" "):
            return
        words = text.split()
        if not words:
            return
        token = words[-1]

        at_reference = token.startswith("@")
        prefix = token[1:] if at_reference else token
        if not at_reference and "/" not in prefix and "\\" not in prefix:
            return

        prefix_lower = prefix.replace("\\", "/").lower()
        for filepath in self._files():
            if filepath.lower().startswith(prefix_lower):
                completion_text = f"@{filepath}" if at_reference else filepath
                yield Completion(
                    completion_text,
                    start_position=-len(token),
                    display_meta="file",
                )


class PhraseCompleter(Completer):
    """Complete a small curated set of starter phrases.

    Offers a phrase when the input so far (lowercased) is a prefix of it.
    Never fires on slash commands or on inputs shorter than two characters.

    Args:
        phrases: Phrases to offer. Defaults to :data:`PHRASES`.
    """

    def __init__(self, phrases: tuple[str, ...] | None = None) -> None:
        self.phrases: tuple[str, ...] = phrases or PHRASES

    def get_completions(
        self, document: Document, complete_event: object
    ) -> Iterable[Completion]:
        text = document.text_before_cursor.lower()
        if text.startswith("/") or len(text) < 2:
            return
        for phrase in self.phrases:
            if phrase.startswith(text) and phrase != text:
                yield Completion(
                    phrase,
                    start_position=-len(text),
                    display_meta="phrase",
                )


def build_chat_completer(work_dir: str | None = None) -> Completer:
    """Return the merged completer used by the chat prompt.

    Args:
        work_dir: Root directory for file-path completion.
    """
    return merge_completers(
        [
            SlashCommandCompleter(),
            FilePathCompleter(root_dir=work_dir),
            PhraseCompleter(),
        ]
    )


def build_chat_session(
    work_dir: str | None = None,
    history_path: str = _DEFAULT_HISTORY_PATH,
) -> PromptSession:
    """Build the PromptSession for the chat loop.

    The chat loop is async — read input with ``session.prompt_async(...)``,
    never the blocking ``prompt()``.

    Args:
        work_dir: Root directory for file-path completion. Defaults to the
            current working directory.
        history_path: History file location; parent directories are created.
    """
    history_file = Path(history_path)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        completer=build_chat_completer(work_dir),
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        complete_while_typing=True,
    )


__all__ = [
    "CHAT_COMMANDS",
    "PHRASES",
    "FilePathCompleter",
    "PhraseCompleter",
    "SlashCommandCompleter",
    "build_chat_completer",
    "build_chat_session",
]
