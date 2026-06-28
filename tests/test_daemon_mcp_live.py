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


# ── Phase 2: secure token auth + redaction ───────────────────────────────────────

_FAKE_TOKEN = "ghp_FAKEsecret0000000000000000000000token"


@pytest.fixture
def clean_github_env():
    """Ensure both github token vars are unset before/after (the daemon sets them
    directly via os.environ, which pytest's monkeypatch wouldn't restore)."""
    import os
    for var in ("GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"):
        os.environ.pop(var, None)
    yield
    for var in ("GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"):
        os.environ.pop(var, None)


def test_write_redacts_registered_secret(tmp_path, restore_stdout):
    """A registered secret never reaches the real stdout (events/responses)."""
    import io
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"))
    daemon._register_secret(_FAKE_TOKEN)
    sink = io.StringIO()
    daemon._real_stdout = sink
    daemon._write({"type": "leak_probe", "payload": {"oops": _FAKE_TOKEN}})
    out = sink.getvalue()
    assert _FAKE_TOKEN not in out
    assert "***REDACTED***" in out


def test_logging_filter_redacts_secret():
    """The redacting log filter scrubs a registered secret from a log record."""
    import logging
    from hcode_v2.daemon.server import _SecretRedactingFilter

    secrets = {_FAKE_TOKEN}
    filt = _SecretRedactingFilter(secrets)
    rec = logging.LogRecord(
        "x", logging.WARNING, "f", 1, "token is %s", (_FAKE_TOKEN,), None
    )
    assert filt.filter(rec) is True
    assert _FAKE_TOKEN not in rec.getMessage()
    assert "***REDACTED***" in rec.getMessage()


def test_apply_secrets_registers_and_sets_env(tmp_path, restore_stdout, clean_github_env):
    import os
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"))
    daemon._apply_mcp_secrets({"secrets": {"GITHUB_TOKEN": _FAKE_TOKEN}})
    assert os.environ.get("GITHUB_TOKEN") == _FAKE_TOKEN   # in-memory env only
    assert _FAKE_TOKEN in daemon._secret_values            # registered for redaction


def test_connect_token_server_needs_auth_without_secret(tmp_path, monkeypatch, restore_stdout, clean_github_env):
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"), mcp_client_factory=_factory(tools=["x"]),
    )
    responses = _capture(daemon, monkeypatch)
    asyncio.run(daemon._handle_connect_mcp_server(1, {"server": "github"}))
    _id, result, error = responses[-1]
    assert error is None
    assert result["status"] == "needs-auth"


def test_github_gate_satisfied_by_either_token_name(tmp_path, monkeypatch, restore_stdout, clean_github_env):
    """The needs-auth gate passes whether the token is under the canonical
    GITHUB_PERSONAL_ACCESS_TOKEN or the legacy GITHUB_TOKEN (alias-aware)."""
    import os
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"))
    gh_def = daemon._server_def("github")

    # No token at all → needs-auth.
    os.environ.pop("GITHUB_PERSONAL_ACCESS_TOKEN", None)
    assert daemon._missing_required_env("github", gh_def) == ["GITHUB_PERSONAL_ACCESS_TOKEN"]

    # Canonical name present → satisfied.
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = "tok"
    assert daemon._missing_required_env("github", gh_def) == []
    os.environ.pop("GITHUB_PERSONAL_ACCESS_TOKEN")

    # Legacy GITHUB_TOKEN present → satisfied via alias.
    os.environ["GITHUB_TOKEN"] = "tok"
    assert daemon._missing_required_env("github", gh_def) == []
    os.environ.pop("GITHUB_TOKEN")


def test_connect_token_server_connects_with_secret(tmp_path, monkeypatch, restore_stdout, clean_github_env):
    """A secret supplied in the connect params lets github pass the gate + connect."""
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"),
        mcp_client_factory=_factory(tools=["create_issue", "search_repositories"]),
    )
    responses = _capture(daemon, monkeypatch)
    asyncio.run(daemon._handle_connect_mcp_server(
        1, {"server": "github", "secrets": {"GITHUB_TOKEN": _FAKE_TOKEN}}
    ))
    _id, result, error = responses[-1]
    assert error is None
    assert result["status"] == "connected"
    assert result["toolCount"] == 2


def test_token_never_appears_in_connect_response(tmp_path, monkeypatch, restore_stdout, clean_github_env):
    """The connect response (what the UI sees) must not contain the token."""
    import json as _json
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(tmp_path / "mcp.json"), mcp_client_factory=_factory(tools=["t"]),
    )
    responses = _capture(daemon, monkeypatch)
    asyncio.run(daemon._handle_connect_mcp_server(
        1, {"server": "github", "secrets": {"GITHUB_TOKEN": _FAKE_TOKEN}}
    ))
    _id, result, _error = responses[-1]
    assert _FAKE_TOKEN not in _json.dumps(result)


def test_token_never_written_to_config(tmp_path, monkeypatch, restore_stdout, clean_github_env):
    """After connecting github with a token, the MCP config holds NO token."""
    cfg = tmp_path / "mcp.json"
    daemon = JsonRpcDaemon(
        mock=False, mcp_config=str(cfg), mcp_client_factory=_factory(tools=["t"]),
    )
    import json as _json
    _capture(daemon, monkeypatch)
    asyncio.run(daemon._handle_connect_mcp_server(
        1, {"server": "github", "secrets": {"GITHUB_TOKEN": _FAKE_TOKEN}}
    ))
    if cfg.exists():
        assert _FAKE_TOKEN not in cfg.read_text()
        # The persisted env block carries no secret.
        entry = _json.loads(cfg.read_text())["servers"].get("github", {})
        assert _FAKE_TOKEN not in _json.dumps(entry)


def test_secret_scrubbed_from_emitted_event_end_to_end(tmp_path, monkeypatch, restore_stdout, clean_github_env):
    """Even if a token is forced into an emitted event, _write scrubs it."""
    import io
    daemon = JsonRpcDaemon(mock=False, mcp_config=str(tmp_path / "mcp.json"))
    daemon._apply_mcp_secrets({"secrets": {"GITHUB_TOKEN": _FAKE_TOKEN}})
    sink = io.StringIO()
    daemon._real_stdout = sink
    daemon.emit_event("task_update", {"markdown": f"using {_FAKE_TOKEN} now"})
    daemon.send_response(7, {"echo": _FAKE_TOKEN})
    assert _FAKE_TOKEN not in sink.getvalue()
