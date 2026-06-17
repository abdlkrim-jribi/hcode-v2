"""Inject MCP servers' required secrets from ``os.environ`` at connect time.

The gap this closes
-------------------
MCP server presets declare ``env_required`` (e.g. ``github`` needs
``GITHUB_TOKEN``), and users put those in ``.env`` — ``load_dotenv`` then places
them in ``os.environ``. But the MCP stdio SDK does **not** forward the full
parent environment to a server subprocess: with no explicit ``env`` it passes
only a fixed safelist (``PATH``, ``APPDATA``, …), and ``GITHUB_TOKEN`` is not on
it. So the token in ``.env`` never reached the github/gitlab servers and they
started unauthenticated.

This HCode-side helper bridges that gap **without modifying libs/deepagents**: it
pulls each server's ``env_required`` vars from ``os.environ`` into the per-server
env right before the client connects, wired in via the manager's
``_client_factory`` hook. The secret stays in ``.env`` / ``os.environ`` and is
never written to the on-disk MCP config.
"""

from __future__ import annotations

import os

from deepagents.mcp.client import KNOWN_SERVERS, MCPClient, MCPServerConfig


def resolve_server_env(server_id: str, config_env: dict) -> dict:
    """Fill a server's env with its required vars from ``os.environ``.

    Looks up the preset's ``env_required`` from :data:`KNOWN_SERVERS` by
    ``server_id``; for each required var not already in ``config_env`` but present
    in ``os.environ``, adds it. Explicit ``config_env`` values win; only the named
    required vars are pulled (no blanket ``os.environ`` forwarding). An unknown
    ``server_id`` has no required vars, so the env is returned unchanged. The
    secret stays in ``.env`` / ``os.environ`` and is never written to disk.

    Args:
        server_id: The configured server id (matches a :data:`KNOWN_SERVERS` key
            for presets, e.g. ``"github"``).
        config_env: The server's env block as loaded from the MCP config.

    Returns:
        A new env dict: ``config_env`` plus any missing required vars found in
        ``os.environ``.
    """
    required = (KNOWN_SERVERS.get(server_id) or {}).get("env_required", [])
    merged = dict(config_env)
    for var in required:
        if var not in merged and var in os.environ:
            merged[var] = os.environ[var]
    return merged


def env_injecting_client_factory(config: MCPServerConfig) -> MCPClient:
    """``MCPClientManager`` ``_client_factory`` hook with secret injection.

    Builds an :class:`MCPClient` whose ``config.env`` has the server's required
    secrets injected from ``os.environ``, without mutating the input ``config``
    or touching ``libs/deepagents``. Constructing the client does not connect
    (no subprocess is spawned until ``connect()``).

    Args:
        config: The per-server config the manager loaded from the MCP config file.

    Returns:
        An :class:`MCPClient` ready to connect with the injected env.
    """
    merged = resolve_server_env(config.server_id, config.env)
    return MCPClient(
        MCPServerConfig(config.server_id, config.command, config.args, merged)
    )
