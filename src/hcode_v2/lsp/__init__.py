"""HCode v2 — Language Server Protocol client (W3.1).

A small, optional, language-agnostic LSP client so the agent can ask a language
server semantic questions about code (diagnostics, definition, references,
hover).  pyright (Python) is the first server; others are added as config.

**Optional by design.**  Nothing here is a hard dependency of the agent or
daemon.  Gate every use on `lsp_available()` and catch `LSPUnavailable` — with no
server installed the layer is simply absent (zero regression).

Example::

    from hcode_v2.lsp import LSPClient, get_config, lsp_available

    if lsp_available("python"):
        async with LSPClient(get_config("python"), project_root) as client:
            for d in await client.get_diagnostics("buggy.py"):
                print(d.severity, d.line, d.message)
"""

from __future__ import annotations

from .client import LSPClient, LSPError, LSPUnavailable
from .config import (
    DEFAULT_LANGUAGE,
    PYRIGHT,
    SERVERS,
    LanguageServerConfig,
    config_for_path,
    get_config,
    lsp_available,
)
from .protocol import (
    Diagnostic,
    DiagnosticSeverity,
    HoverResult,
    Location,
    Position,
    Range,
    canonical_key,
    path_to_uri,
    uri_to_path,
)

__all__ = [
    # client
    "LSPClient",
    "LSPError",
    "LSPUnavailable",
    # config / registry
    "LanguageServerConfig",
    "PYRIGHT",
    "SERVERS",
    "DEFAULT_LANGUAGE",
    "get_config",
    "config_for_path",
    "lsp_available",
    # protocol types
    "Diagnostic",
    "DiagnosticSeverity",
    "HoverResult",
    "Location",
    "Position",
    "Range",
    "path_to_uri",
    "uri_to_path",
    "canonical_key",
]
