"""Unit tests for the MCP client and bridge modules."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import create_model

from deepagents.mcp.bridge import MCPToolBridge, MCPToolRegistry, _build_args_schema
from deepagents.mcp.client import MCPClient, MCPClientManager, MCPServerConfig, MCPToolInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_mock(name: str, description: str, schema: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock MCP tool as returned by session.list_tools()."""
    m = MagicMock()
    m.name = name
    m.description = description
    m.inputSchema = schema or {}
    return m


def _make_session(tools: list[MagicMock] | None = None) -> MagicMock:
    """Build a mock MCP session with list_tools and call_tool pre-configured."""
    session = MagicMock()
    list_result = MagicMock()
    list_result.tools = tools or []
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock()
    return session


class _FakeClient:
    """Minimal fake MCPClient for manager tests — avoids subprocess startup."""

    def __init__(self, config: MCPServerConfig, *, should_fail: bool = False) -> None:
        self.config = config
        self._should_fail = should_fail
        self._tools: list[MCPToolInfo] = []
        self.disconnected = False

    async def connect(self) -> None:
        if self._should_fail:
            msg = "connection refused"
            raise OSError(msg)
        self._tools = [MCPToolInfo(f"{self.config.server_id}_tool", "desc", {})]

    async def disconnect(self) -> None:
        self.disconnected = True

    @property
    def tools(self) -> list[MCPToolInfo]:
        return list(self._tools)

    @property
    def server_id(self) -> str:
        return self.config.server_id


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    def test_construction_stores_fields(self) -> None:
        cfg = MCPServerConfig("srv", "npx", ["--arg"], {"KEY": "val"})
        assert cfg.server_id == "srv"
        assert cfg.command == "npx"
        assert cfg.args == ["--arg"]
        assert cfg.env == {"KEY": "val"}

    def test_defaults_for_optional_fields(self) -> None:
        cfg = MCPServerConfig("srv", "cmd")
        assert cfg.args == []
        assert cfg.env == {}


# ---------------------------------------------------------------------------
# MCPToolInfo
# ---------------------------------------------------------------------------


class TestMCPToolInfo:
    def test_construction_stores_all_fields(self) -> None:
        schema = {"properties": {"path": {"type": "string"}}}
        info = MCPToolInfo("read", "Read a file", schema)
        assert info.name == "read"
        assert info.description == "Read a file"
        assert info.input_schema == schema


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class TestMCPClient:
    async def test_connect_discovers_tools(self) -> None:
        tool_mock = _make_tool_mock(
            "read_file",
            "Read a file",
            {"properties": {"path": {"type": "string"}}, "required": ["path"]},
        )
        session = _make_session([tool_mock])
        config = MCPServerConfig("test", "test-server")
        client = MCPClient(config, _session=session)
        await client.connect()

        assert len(client.tools) == 1
        assert client.tools[0].name == "read_file"
        assert client.tools[0].description == "Read a file"

    async def test_connect_with_empty_tool_list(self) -> None:
        session = _make_session([])
        client = MCPClient(MCPServerConfig("s", "cmd"), _session=session)
        await client.connect()
        assert client.tools == []

    async def test_call_tool_returns_text_content(self) -> None:
        session = _make_session([])
        content_item = MagicMock()
        content_item.text = "hello world"
        call_result = MagicMock()
        call_result.content = [content_item]
        session.call_tool = AsyncMock(return_value=call_result)

        client = MCPClient(MCPServerConfig("s", "cmd"), _session=session)
        await client.connect()
        result = await client.call_tool("read_file", {"path": "foo.txt"})

        assert result == "hello world"
        session.call_tool.assert_called_once_with("read_file", {"path": "foo.txt"})

    async def test_call_tool_joins_multiple_items(self) -> None:
        session = _make_session([])
        items = [MagicMock(text="line1"), MagicMock(text="line2")]
        call_result = MagicMock(content=items)
        session.call_tool = AsyncMock(return_value=call_result)

        client = MCPClient(MCPServerConfig("s", "cmd"), _session=session)
        await client.connect()
        result = await client.call_tool("tool", {})

        assert result == "line1\nline2"

    async def test_disconnect_clears_session(self) -> None:
        session = _make_session([])
        client = MCPClient(MCPServerConfig("s", "cmd"), _session=session)
        await client.connect()
        await client.disconnect()

        assert client._session is None

    def test_server_id_property(self) -> None:
        client = MCPClient(MCPServerConfig("my-server", "cmd"))
        assert client.server_id == "my-server"


# ---------------------------------------------------------------------------
# MCPClientManager
# ---------------------------------------------------------------------------


class TestMCPClientManager:
    def _write_config(self, tmp_path: Any, servers: dict[str, Any]) -> str:
        p = tmp_path / "mcp_config.json"
        p.write_text(json.dumps({"servers": servers}))
        return str(p)

    async def test_connect_all_loads_two_servers(self, tmp_path: Any) -> None:
        path = self._write_config(tmp_path, {
            "server1": {"command": "cmd1"},
            "server2": {"command": "cmd2", "args": ["--flag"]},
        })
        manager = MCPClientManager(path, _client_factory=_FakeClient)
        await manager.connect_all()

        assert set(manager.connected_servers) == {"server1", "server2"}

    async def test_connect_all_skips_failed_server(self, tmp_path: Any) -> None:
        path = self._write_config(tmp_path, {
            "good": {"command": "ok"},
            "bad": {"command": "broken"},
        })

        def factory(config: MCPServerConfig) -> _FakeClient:
            return _FakeClient(config, should_fail=(config.server_id == "bad"))

        manager = MCPClientManager(path, _client_factory=factory)
        await manager.connect_all()

        assert manager.connected_servers == ["good"]

    async def test_missing_config_file_gives_no_servers(self, tmp_path: Any) -> None:
        manager = MCPClientManager(str(tmp_path / "nonexistent.json"), _client_factory=_FakeClient)
        await manager.connect_all()
        assert manager.connected_servers == []

    async def test_get_all_tools_aggregates_across_servers(self, tmp_path: Any) -> None:
        path = self._write_config(tmp_path, {
            "s1": {"command": "c1"},
            "s2": {"command": "c2"},
        })
        manager = MCPClientManager(path, _client_factory=_FakeClient)
        await manager.connect_all()

        tools = manager.get_all_tools()
        tool_names = {t.name for _, t in tools}
        assert tool_names == {"s1_tool", "s2_tool"}

    async def test_disconnect_all_clears_clients(self, tmp_path: Any) -> None:
        path = self._write_config(tmp_path, {"srv": {"command": "cmd"}})
        manager = MCPClientManager(path, _client_factory=_FakeClient)
        await manager.connect_all()

        assert len(manager.connected_servers) == 1
        await manager.disconnect_all()
        assert manager.connected_servers == []

    def test_get_client_returns_none_for_unknown(self) -> None:
        manager = MCPClientManager(_client_factory=_FakeClient)
        assert manager.get_client("missing") is None


# ---------------------------------------------------------------------------
# _build_args_schema
# ---------------------------------------------------------------------------


class TestBuildArgsSchema:
    def test_required_string_field(self) -> None:
        schema = _build_args_schema({
            "properties": {"path": {"type": "string", "description": "File path"}},
            "required": ["path"],
        })
        instance = schema(path="foo.txt")
        assert instance.path == "foo.txt"

    def test_optional_field_defaults_to_none(self) -> None:
        schema = _build_args_schema({
            "properties": {"offset": {"type": "integer"}},
        })
        instance = schema()
        assert instance.offset is None

    def test_empty_schema_creates_empty_model(self) -> None:
        schema = _build_args_schema({})
        instance = schema()
        assert instance is not None

    def test_required_field_raises_on_missing(self) -> None:
        schema = _build_args_schema({
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        })
        with pytest.raises(Exception):  # ValidationError
            schema()


# ---------------------------------------------------------------------------
# MCPToolBridge
# ---------------------------------------------------------------------------


class TestMCPToolBridge:
    async def test_arun_delegates_to_client(self) -> None:
        client = MagicMock()
        client.call_tool = AsyncMock(return_value="tool result")

        bridge = MCPToolBridge(
            name="mcp_srv_tool",
            description="A test tool",
            args_schema=create_model("Args"),
            mcp_client=client,
            mcp_tool_name="tool",
        )
        result = await bridge._arun(key="value")

        assert result == "tool result"
        client.call_tool.assert_called_once_with("tool", {"key": "value"})

    def test_name_format_includes_server_and_tool(self) -> None:
        bridge = MCPToolBridge(
            name="mcp_myserver_read_file",
            description="desc",
            args_schema=create_model("Args"),
            mcp_client=MagicMock(),
            mcp_tool_name="read_file",
        )
        assert bridge.name == "mcp_myserver_read_file"

    def test_mcp_tool_name_stored_separately(self) -> None:
        bridge = MCPToolBridge(
            name="mcp_s_t",
            description="d",
            args_schema=create_model("Args"),
            mcp_client=MagicMock(),
            mcp_tool_name="original_tool",
        )
        assert bridge.mcp_tool_name == "original_tool"


# ---------------------------------------------------------------------------
# MCPToolRegistry
# ---------------------------------------------------------------------------


class TestMCPToolRegistry:
    def _make_manager(self, server_id: str, tool_name: str) -> MagicMock:
        tool_info = MCPToolInfo(tool_name, "desc", {})
        client = MagicMock()
        manager = MagicMock()
        manager.get_all_tools.return_value = [(server_id, tool_info)]
        manager.get_client.return_value = client
        return manager

    def test_build_tools_returns_one_bridge_per_tool(self) -> None:
        manager = self._make_manager("server1", "read")
        tools = MCPToolRegistry.build_tools(manager)

        assert len(tools) == 1
        assert tools[0].name == "mcp_server1_read"
        assert tools[0].description == "desc"

    def test_build_tools_skips_when_client_missing(self) -> None:
        tool_info = MCPToolInfo("t", "d", {})
        manager = MagicMock()
        manager.get_all_tools.return_value = [("s", tool_info)]
        manager.get_client.return_value = None

        tools = MCPToolRegistry.build_tools(manager)
        assert tools == []

    def test_get_tools_delegates_to_build_tools(self) -> None:
        manager = self._make_manager("s", "tool")
        registry = MCPToolRegistry(manager)
        tools = registry.get_tools()

        assert len(tools) == 1
        assert tools[0].name == "mcp_s_tool"
