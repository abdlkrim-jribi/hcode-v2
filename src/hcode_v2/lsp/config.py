"""Language-server registry — the pluggable part of the LSP layer.

Adding a new language is *config, not code*: append a `LanguageServerConfig` to
`SERVERS`.  The client core (`client.py`) is entirely language-agnostic and only
ever reads these descriptors.

`pyright` ships first because the agent primarily writes Python.  `tsserver`,
`rust-analyzer`, `gopls`, … slot in the same way later.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LanguageServerConfig:
    """How to launch one language server and which LSP ``languageId`` it serves.

    `command` is looked up on ``PATH`` (respecting ``PATHEXT`` on Windows, so an
    npm ``.cmd`` shim resolves correctly).  Resolution is *lazy* — a config can
    exist for a server that isn't installed; `resolve_argv` simply returns
    ``None`` and the layer degrades gracefully.
    """
    language_id: str
    command: str
    args: tuple[str, ...] = ()
    # File extensions this server handles, e.g. (".py",) — used to pick a server.
    extensions: tuple[str, ...] = ()

    def resolve_argv(self) -> Optional[list[str]]:
        """Full argv to spawn, or ``None`` if the server binary isn't on PATH."""
        exe = shutil.which(self.command)
        if exe is None:
            return None
        return [exe, *self.args]

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None


# ── Built-in registry ─────────────────────────────────────────────────────────

PYRIGHT = LanguageServerConfig(
    language_id="python",
    command="pyright-langserver",
    args=("--stdio",),
    extensions=(".py", ".pyi"),
)

# language_id → config.  Extend this list to add a language.
SERVERS: dict[str, LanguageServerConfig] = {
    PYRIGHT.language_id: PYRIGHT,
}

# Default language for the W3 work (the agent writes Python).
DEFAULT_LANGUAGE = "python"


def get_config(language_id: str = DEFAULT_LANGUAGE) -> Optional[LanguageServerConfig]:
    """Return the server config for *language_id*, or ``None`` if unregistered."""
    return SERVERS.get(language_id)


def config_for_path(path: str) -> Optional[LanguageServerConfig]:
    """Pick a server config by file extension, or ``None`` if none matches."""
    lowered = path.lower()
    for cfg in SERVERS.values():
        if any(lowered.endswith(ext) for ext in cfg.extensions):
            return cfg
    return None


def lsp_available(language_id: str = DEFAULT_LANGUAGE) -> bool:
    """``True`` iff a server is registered AND its binary is installed.

    This is the capability gate the whole layer hangs off: tools and the future
    PEV-Verify hook call this first and no-op cleanly when it is ``False`` — so a
    machine with no language server behaves exactly as if LSP did not exist.
    """
    cfg = SERVERS.get(language_id)
    return cfg is not None and cfg.is_available()
