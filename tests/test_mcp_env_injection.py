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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from deepagents.mcp.client import MCPClient, MCPServerConfig

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


# --- arg cleaning: omit None-valued optionals from MCP tool calls -----------
#
# langchain materializes every unset optional as None (via model_dump on the
# bridge's args_schema) and the bridge forwards the whole dict to call_tool — so
# the github server gets {direction: null, labels: null, ...} and its zod schema
# rejects null. The factory's client must strip None-valued args before the call,
# sending those optionals ABSENT (what zod wants). Env injection must still work.


def _gh_config() -> MCPServerConfig:
    return MCPServerConfig("github", "npx", ["-y", "@modelcontextprotocol/server-github"], {})


def _client_with_mock_session() -> tuple[MCPClient, MagicMock]:
    """Factory-built client with a fake MCP session injected (no subprocess).

    The session's call_tool returns an empty-content result so MCPClient.call_tool
    completes; the mock records exactly what arguments reached the transport.
    """
    client = env_injecting_client_factory(_gh_config())
    session = MagicMock()
    session.call_tool = AsyncMock(return_value=SimpleNamespace(content=[]))
    client._session = session  # MCPClient stores the session as a plain attr
    return client, session


def test_factory_returns_arg_cleaning_client() -> None:
    client = env_injecting_client_factory(_gh_config())
    # still a real MCPClient, but the None-stripping subclass (not plain MCPClient)
    assert isinstance(client, MCPClient)
    assert type(client) is not MCPClient


async def test_call_tool_strips_none_args() -> None:
    client, session = _client_with_mock_session()
    await client.call_tool(
        "list_issues",
        {"owner": "o", "repo": "r", "direction": None, "labels": None, "page": None},
    )
    # the None-valued optionals are dropped — only real args reach the transport
    session.call_tool.assert_called_once_with("list_issues", {"owner": "o", "repo": "r"})


async def test_call_tool_keeps_falsy_non_none() -> None:
    client, session = _client_with_mock_session()
    await client.call_tool("t", {"a": 0, "b": "", "c": False, "d": None})
    # only None (d) is stripped; 0 / "" / False are legitimate values and kept
    session.call_tool.assert_called_once_with("t", {"a": 0, "b": "", "c": False})


def test_env_injection_still_works_on_cleaning_client(monkeypatch) -> None:
    # the arg-cleaning subclass must NOT regress the token injection
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    client = env_injecting_client_factory(_gh_config())
    assert client.config.env["GITHUB_TOKEN"] == "x"


# ── connect-failure stderr summarization (native diagnosability) ───────────────

from hcode_v2.agent.mcp_env import (  # noqa: E402
    McpConnectError,
    _summarize_connect_failure,
    capturing_client_factory,
)


def _cfg(command="npx", args=None) -> MCPServerConfig:
    return MCPServerConfig("web-fetch", command, args or ["-y", "@x/server-fetch"], {})


def test_summarize_detects_missing_package() -> None:
    # An npm 404 must become an actionable "package not found" message — this is
    # the real native failure that previously showed only "Connection closed".
    stderr = ("npm error code E404\n"
              "npm error 404 Not Found - GET https://registry.npmjs.org/@x%2fserver-fetch\n"
              "npm error 404  '@x/server-fetch@*' is not in this registry.")
    msg = _summarize_connect_failure(_cfg(), stderr, RuntimeError("Connection closed"))
    assert "not found" in msg.lower()
    assert "registry" in msg.lower()  # the captured stderr tail is included


def test_summarize_detects_missing_command() -> None:
    msg = _summarize_connect_failure(
        _cfg(command="uvx"), "'uvx' is not recognized as an internal or external command",
        RuntimeError("Connection closed"),
    )
    assert "not found" in msg.lower()


def test_summarize_falls_back_to_exception_when_no_stderr() -> None:
    msg = _summarize_connect_failure(_cfg(), "", RuntimeError("boom"))
    assert "boom" in msg


def test_capturing_client_factory_builds_capturing_client(monkeypatch) -> None:
    from hcode_v2.agent.mcp_env import _CapturingMCPClient
    client = capturing_client_factory(_cfg())
    assert isinstance(client, _CapturingMCPClient)


async def test_capturing_client_raises_diagnostic_on_bad_command() -> None:
    # A command that cannot start (bogus binary) must raise McpConnectError with
    # a diagnostic message, NOT hang and NOT a bare "Connection closed". Fast:
    # the spawn fails immediately, no network/download.
    client = capturing_client_factory(
        MCPServerConfig("broken", "hcode-no-such-binary-xyz", [], {})
    )
    with pytest.raises(McpConnectError):
        await client.connect()
