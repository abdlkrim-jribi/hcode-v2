"""In-process tests for the daemon's live MCP connection handling (Phase 1).

These instantiate ``JsonRpcDaemon`` directly and inject a fake client factory so
NO real subprocess (npx/node/network) is spawned — the live-connection machinery
is exercised end-to-end against fakes. The daemon's __init__ swaps sys.stdout for
stderr, so a fixture restores it.

What's proven here:
  - connect spawns a client, runs its connect(), and reports the REAL tool count
    from the connected client (not a hardcoded "connected").
  - a token-required server with no secret is surfaced as needs-auth (not spawned).
  - a connect failure surfaces the real error message and an error status in list.
  - list_mcp_servers annotates each server with live status + tool count.
  - disconnect tears the live client down.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from hcode_v2.daemon.server import JsonRpcDaemon


# ── Fakes ──────────────────────────────────────────────────────────────────────

class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMCPClient:
    """Stand-in MCPClient: no subprocess, configurable tools / failure."""

    def __init__(self, config, *, tools=None, fail: str | None = None) -> None:
        self.config = config
        self._tools = [_FakeTool(t) for t in (tools or [])]
        self._fail = fail
        self.disconnected = False

    async def connect(self) -> None:
        if self._fail:
            raise RuntimeError(self._fail)

    @property
    def tools(self):
        return list(self._tools)

    async def disconnect(self) -> None:
        self.disconnected = True


def _factory(tools=None, fail: str | None = None):
    """Build an mcp_client_factory that returns a fake client."""
    return lambda config: _FakeMCPClient(config, tools=tools, fail=fail)


@pytest.fixture
def restore_stdout():
    orig = sys.stdout
    yield
    sys.stdout = orig


def _capture(daemon, monkeypatch) -> list[tuple]:
    responses: list[tuple] = []
    monkeypatch.setattr(
        daemon, "send_response",
        lambda req_id, result=None, error=None: responses.append((req_id, result, error)),
    )
    return responses


# ── connect: real tool count ────────────────────────────────────────────────────

def test_connect_reports_live_tool_count(tmp_path, monkeypatch, restore_stdout):
    cfg = str(tmp_path / "mcp.json")
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=cfg,
        mcp_client_factory=_factory(tools=["fetch", "fetch_html", "fetch_json"]),
    )
    responses = _capture(daemon, monkeypatch)

    async def _drive():
        # web-fetch is a no-auth catalog server.
        await daemon._handle_connect_mcp_server(10, {"server": "web-fetch"})

    asyncio.run(_drive())

    _id, result, error = responses[-1]
    assert error is None
    assert result["status"] == "connected"
    # The count is the REAL number of tools the (fake) client discovered — 3 —
    # proving the daemon reads the live connection, not a hardcoded value.
    assert result["toolCount"] == 3
    assert result["tools"] == ["fetch", "fetch_html", "fetch_json"]
    # The live client is held by the daemon.
    assert "web-fetch" in daemon._mcp_clients


def test_connect_persists_known_server_to_config(tmp_path, monkeypatch, restore_stdout):
    import json
    cfg = tmp_path / "mcp.json"
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(cfg), mcp_client_factory=_factory(tools=["fetch"]),
    )
    _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(1, {"server": "web-fetch"}))

    # Connecting a known server writes it to config so the agent picks it up.
    assert cfg.exists()
    assert "web-fetch" in json.loads(cfg.read_text())["servers"]


# ── connect: needs-auth (token server, Phase 2) ──────────────────────────────────

def test_connect_token_server_returns_needs_auth(tmp_path, monkeypatch, restore_stdout):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = tmp_path / "mcp.json"
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(cfg), mcp_client_factory=_factory(tools=["x"]),
    )
    responses = _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(2, {"server": "github"}))

    _id, result, error = responses[-1]
    assert error is None
    assert result["status"] == "needs-auth"
    # Not spawned, not held, not written to config.
    assert "github" not in daemon._mcp_clients
    assert not cfg.exists()


def test_connect_token_server_connects_when_secret_present(tmp_path, monkeypatch, restore_stdout):
    # The existing CLI/.env path must keep working: if the secret IS in env,
    # Phase 1 connects (no NEW auth code — just reading existing env).
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"),
        mcp_client_factory=_factory(tools=["create_issue"]),
    )
    responses = _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(3, {"server": "github"}))

    _id, result, error = responses[-1]
    assert error is None
    assert result["status"] == "connected"
    assert result["toolCount"] == 1


# ── connect: error path ──────────────────────────────────────────────────────────

def test_connect_failure_surfaces_real_error(tmp_path, monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"),
        mcp_client_factory=_factory(fail="ENOENT: npx not found"),
    )
    responses = _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(4, {"server": "web-fetch"}))

    _id, result, error = responses[-1]
    assert error is not None
    assert "npx not found" in error["message"]
    # The error is remembered so list_mcp_servers can show an error status.
    assert daemon._mcp_errors.get("web-fetch") == "ENOENT: npx not found"
    assert "web-fetch" not in daemon._mcp_clients


def test_connect_unknown_server_errors(tmp_path, monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"))
    responses = _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(5, {"server": "nope-not-real"}))

    _id, result, error = responses[-1]
    assert error is not None
    assert error["code"] == -32602


# ── list: live status annotation ────────────────────────────────────────────────

def test_list_annotates_connected_status(tmp_path, monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"),
        mcp_client_factory=_factory(tools=["fetch", "fetch_html"]),
    )
    responses = _capture(daemon, monkeypatch)

    async def _drive():
        await daemon._handle_connect_mcp_server(1, {"server": "web-fetch"})
        await daemon._handle_list_mcp_servers(2)

    asyncio.run(_drive())

    _id, result, _err = responses[-1]
    by_name = {s["name"]: s for s in result["servers"]}
    assert by_name["web-fetch"]["status"] == "connected"
    assert by_name["web-fetch"]["toolCount"] == 2
    # A token server with no secret shows needs-auth in the same listing.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert by_name["github"]["status"] == "needs-auth"


def test_list_annotates_error_status(tmp_path, monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"),
        mcp_client_factory=_factory(fail="boom"),
    )
    responses = _capture(daemon, monkeypatch)

    async def _drive():
        await daemon._handle_connect_mcp_server(1, {"server": "web-fetch"})
        await daemon._handle_list_mcp_servers(2)

    asyncio.run(_drive())

    _id, result, _err = responses[-1]
    by_name = {s["name"]: s for s in result["servers"]}
    assert by_name["web-fetch"]["status"] == "error"
    assert "boom" in by_name["web-fetch"]["errorMessage"]


# ── disconnect: teardown ─────────────────────────────────────────────────────────

def test_disconnect_tears_down_live_client(tmp_path, monkeypatch, restore_stdout):
    factory = _factory(tools=["fetch"])
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"), mcp_client_factory=factory)
    responses = _capture(daemon, monkeypatch)

    async def _drive():
        await daemon._handle_connect_mcp_server(1, {"server": "web-fetch"})
        client = daemon._mcp_clients["web-fetch"]
        await daemon._handle_disconnect_mcp_server(2, {"server": "web-fetch"})
        return client

    client = asyncio.run(_drive())

    _id, result, error = responses[-1]
    assert error is None
    assert result["status"] == "disconnected"
    assert client.disconnected is True
    assert "web-fetch" not in daemon._mcp_clients


# ── cache eviction signature ─────────────────────────────────────────────────────

def test_mcp_config_signature_tracks_configured_servers(tmp_path, restore_stdout):
    import json
    cfg = tmp_path / "mcp.json"
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(cfg))
    # No file → empty signature.
    assert daemon._mcp_config_signature() == frozenset()
    cfg.write_text(json.dumps({"servers": {"web-fetch": {"command": "npx", "args": []}}}))
    # File with one server → signature contains it.
    assert daemon._mcp_config_signature() == frozenset({"web-fetch"})


# ── native-connect fixes: corrected catalog + diagnostic errors ──────────────────

def test_default_factory_is_capturing(tmp_path, restore_stdout):
    """The daemon's default MCP factory captures stderr so failures are diagnosable."""
    from hcode_v2.agent.mcp_env import capturing_client_factory
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"))
    assert daemon._resolve_mcp_factory() is capturing_client_factory


def test_connect_persists_corrected_uvx_command(tmp_path, monkeypatch, restore_stdout):
    """Connecting web-fetch writes the CORRECTED uvx command to config (not the
    vendored 404 npm package) so the agent spawns the working server too."""
    import json
    cfg = tmp_path / "mcp.json"
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(cfg), mcp_client_factory=_factory(tools=["fetch"]),
    )
    _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(1, {"server": "web-fetch"}))

    entry = json.loads(cfg.read_text())["servers"]["web-fetch"]
    assert entry["command"] == "uvx"
    assert entry["args"] == ["mcp-server-fetch"]


def test_connect_failure_returns_diagnostic_not_connection_closed(tmp_path, monkeypatch, restore_stdout):
    """A failed connect surfaces the capturing client's diagnostic message
    (the real reason), not the SDK's opaque 'Connection closed'."""
    from hcode_v2.agent.mcp_env import McpConnectError
    diagnostic = "Package not found — `uvx mcp-server-fetch` resolves to a package that does not exist."

    def _raising_factory(config):
        class _C:
            config = None
            async def connect(self):
                raise McpConnectError(diagnostic)
            @property
            def tools(self):
                return []
            async def disconnect(self):
                pass
        return _C()

    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"), mcp_client_factory=_raising_factory,
    )
    responses = _capture(daemon, monkeypatch)

    asyncio.run(daemon._handle_connect_mcp_server(1, {"server": "web-fetch"}))

    _id, result, error = responses[-1]
    assert error is not None
    assert "not found" in error["message"].lower()
    assert error["message"] != "Connection closed"
    # And the error is remembered for list_mcp_servers' error status.
    assert "web-fetch" in daemon._mcp_errors


def test_one_bad_server_does_not_break_the_daemon(tmp_path, monkeypatch, restore_stdout):
    """A connect that raises a BaseException (anyio teardown) is isolated — the
    daemon keeps serving (next request still handled)."""
    class _Boom(BaseException):
        pass

    def _boom_factory(config):
        class _C:
            async def connect(self):
                raise _Boom("anyio teardown exploded")
            @property
            def tools(self):
                return []
            async def disconnect(self):
                pass
        return _C()

    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"), mcp_client_factory=_boom_factory,
    )
    responses = _capture(daemon, monkeypatch)

    async def _drive():
        # The bad connect must NOT propagate out of the handler.
        await daemon._handle_connect_mcp_server(1, {"server": "web-fetch"})
        # The daemon still handles the next request.
        await daemon._handle_health(2)

    asyncio.run(_drive())

    _id, result, _error = responses[-1]
    assert result["status"] == "running"  # health still works
