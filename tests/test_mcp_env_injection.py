"""Tests for HCode's MCP env-injection helper (``hcode_v2.agent.mcp_env``).

The gap this closes: ``.env`` holds ``GITHUB_TOKEN``, ``load_dotenv`` puts it in
``os.environ``, but the MCP SDK only forwards a fixed safelist to the server
subprocess — so the token never reaches the github/gitlab servers. The helper
injects each preset's ``env_required`` vars from ``os.environ`` into the per-server
env *before* connect, keeping the secret in ``.env`` (never on disk), and is wired
on the HCode side via ``MCPClientManager(_client_factory=...)`` so ``libs/deepagents``
stays frozen.

Contract (to be implemented in ``hcode_v2/agent/mcp_env.py``):

* ``resolve_server_env(server_id, config_env) -> dict``
    required = (KNOWN_SERVERS.get(server_id) or {}).get("env_required", [])
    merged = dict(config_env); for each required var NOT already in merged but
    present in os.environ, add it. Config values win; only the named required vars
    are injected (no blanket os.environ forwarding).
* ``env_injecting_client_factory(config) -> MCPClient``
    builds a fresh MCPServerConfig whose env = resolve_server_env(config.server_id,
    config.env) and returns MCPClient(that). Constructing MCPClient does NOT spawn
    a subprocess (connect() is separate), so this is CI-safe.

These FAIL now: ``hcode_v2.agent.mcp_env`` does not exist yet (ImportError).
"""

from __future__ import annotations

from deepagents.mcp.client import MCPServerConfig

from hcode_v2.agent.mcp_env import env_injecting_client_factory, resolve_server_env


def test_resolve_injects_required_from_environ(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    result = resolve_server_env("github", {})
    assert result["GITHUB_TOKEN"] == "x"


def test_config_env_wins_over_environ(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "from_env")
    result = resolve_server_env("github", {"GITHUB_TOKEN": "from_config"})
    assert result["GITHUB_TOKEN"] == "from_config"


def test_non_required_vars_not_injected(monkeypatch) -> None:
    monkeypatch.setenv("UNRELATED", "y")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = resolve_server_env("github", {})
    # no blanket forwarding, and a missing required var is simply absent
    assert "UNRELATED" not in result
    assert "GITHUB_TOKEN" not in result


def test_unknown_server_unchanged(monkeypatch) -> None:
    # a server id not in KNOWN_SERVERS has no env_required -> nothing injected
    assert resolve_server_env("custom-id-not-in-catalog", {"A": "1"}) == {"A": "1"}


def test_gitlab_injects_both(monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    monkeypatch.setenv("GITLAB_URL", "u")
    result = resolve_server_env("gitlab", {})
    assert result["GITLAB_TOKEN"] == "t"
    assert result["GITLAB_URL"] == "u"


def test_no_token_server_stays_empty(monkeypatch) -> None:
    # filesystem has env_required == [] -> empty config env stays empty
    assert resolve_server_env("filesystem", {}) == {}


def test_factory_returns_client_with_injected_env(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    config = MCPServerConfig(
        "github", "npx", ["-y", "@modelcontextprotocol/server-github"], {}
    )
    client = env_injecting_client_factory(config)  # no connect() -> no subprocess
    assert client.config.env["GITHUB_TOKEN"] == "x"
