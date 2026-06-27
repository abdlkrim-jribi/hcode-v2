"""HCode-side MCP server catalog — corrects the vendored KNOWN_SERVERS commands.

Why this exists
---------------
The vendored ``deepagents`` ``KNOWN_SERVERS`` catalog ships a couple of presets
whose commands do not resolve on a real machine:

  - ``web-fetch``  → ``npx -y @modelcontextprotocol/server-fetch``
  - ``sqlite-mcp`` → ``npx -y @modelcontextprotocol/server-sqlite``

Neither npm package exists (``npm error 404 … is not in this registry``), so a
native connect spawns ``npx``, the process exits immediately, and the MCP session
dies with an opaque *"Connection closed"*. The real reference servers for fetch
and sqlite are **Python** packages run via ``uvx`` (``mcp-server-fetch`` and
``mcp-server-sqlite``), both verified to connect no-auth.

We must not edit ``libs/deepagents`` (vendored), so these corrections are applied
HCode-side: the daemon resolves server definitions through :func:`merged_catalog`
(corrections overlaid on the vendored catalog) for connect, listing, and the
config entry it persists — so the agent picks up the working command too.
"""

from __future__ import annotations

# Corrected / verified-working presets, overlaid on the vendored KNOWN_SERVERS.
# Each entry mirrors the vendored shape (command/args/env/env_required/description).
CATALOG_OVERRIDES: dict[str, dict] = {
    "web-fetch": {
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env": {},
        "env_required": [],
        "description": "Web fetch — fetch a URL and convert it to markdown (uvx mcp-server-fetch)",
    },
    "sqlite-mcp": {
        "command": "uvx",
        # --db-path is required by the server; default to a file in the work dir
        # (the daemon has chdir'd into work_dir), created on first use.
        "args": ["mcp-server-sqlite", "--db-path", "mcp_sqlite.db"],
        "env": {},
        "env_required": [],
        "description": "SQLite — query/manage a local SQLite DB (uvx mcp-server-sqlite)",
    },
}


def merged_catalog() -> dict[str, dict]:
    """Return the vendored KNOWN_SERVERS with HCode corrections overlaid."""
    from deepagents.mcp.client import KNOWN_SERVERS

    merged: dict[str, dict] = {name: dict(cfg) for name, cfg in KNOWN_SERVERS.items()}
    for name, override in CATALOG_OVERRIDES.items():
        merged[name] = {**merged.get(name, {}), **override}
    return merged


def known_server(name: str) -> dict | None:
    """Return the corrected preset for *name*, or ``None`` if not a known preset."""
    return merged_catalog().get(name)


__all__ = ["CATALOG_OVERRIDES", "merged_catalog", "known_server"]
