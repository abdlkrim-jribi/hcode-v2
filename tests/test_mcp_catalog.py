"""Tests for the HCode-side MCP catalog corrections (hcode_v2.agent.mcp_catalog).

The vendored KNOWN_SERVERS ships fetch/sqlite commands that point at npm packages
which don't exist (npx → 404). These corrections overlay the catalog with the real
uvx-based servers WITHOUT editing libs/deepagents.
"""

from __future__ import annotations

from hcode_v2.agent.mcp_catalog import CATALOG_OVERRIDES, known_server, merged_catalog


def test_web_fetch_corrected_to_uvx() -> None:
    fetch = known_server("web-fetch")
    assert fetch is not None
    assert fetch["command"] == "uvx"
    assert fetch["args"] == ["mcp-server-fetch"]
    assert fetch["env_required"] == []  # no auth


def test_sqlite_corrected_to_uvx() -> None:
    sqlite = known_server("sqlite-mcp")
    assert sqlite is not None
    assert sqlite["command"] == "uvx"
    assert sqlite["args"][0] == "mcp-server-sqlite"
    assert "--db-path" in sqlite["args"]
    assert sqlite["env_required"] == []


def test_no_longer_uses_nonexistent_npm_packages() -> None:
    # The whole point: the corrected commands must NOT be the 404 npm packages.
    for name in ("web-fetch", "sqlite-mcp"):
        defn = known_server(name)
        joined = " ".join([defn["command"], *defn["args"]])
        assert "@modelcontextprotocol/server-fetch" not in joined
        assert "@modelcontextprotocol/server-sqlite" not in joined


def test_vendored_entries_pass_through_unchanged() -> None:
    # Servers we don't override keep their vendored definition (e.g. filesystem
    # works as-is; gitlab stays a token+url server).
    from deepagents.mcp.client import KNOWN_SERVERS

    fs = known_server("filesystem")
    assert fs["command"] == KNOWN_SERVERS["filesystem"]["command"]
    gl = known_server("gitlab")
    assert gl["env_required"] == KNOWN_SERVERS["gitlab"]["env_required"]


def test_github_override_uses_personal_access_token() -> None:
    # The npm @modelcontextprotocol/server-github reads GITHUB_PERSONAL_ACCESS_TOKEN,
    # not the vendored GITHUB_TOKEN — the override corrects env_required so the gate
    # and the injected env match what the server actually reads.
    gh = known_server("github")
    assert gh["env_required"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]


def test_merged_catalog_is_superset_of_vendored() -> None:
    from deepagents.mcp.client import KNOWN_SERVERS

    merged = merged_catalog()
    assert set(KNOWN_SERVERS).issubset(set(merged))
    # Overrides are a subset of the merged catalog keys.
    assert set(CATALOG_OVERRIDES).issubset(set(merged))


def test_unknown_server_returns_none() -> None:
    assert known_server("definitely-not-real") is None
