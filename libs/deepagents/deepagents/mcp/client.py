"""MCP (Model Context Protocol) client — connects to external tool servers via stdio transport."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH: str = ".hcode/mcp_config.json"


class MCPServerConfig:
    """Configuration for a single MCP server.

    Args:
        server_id: Unique identifier for this server.
        command: Executable to launch the server subprocess.
        args: Command-line arguments passed to the subprocess.
        env: Additional environment variables for the subprocess.
    """

    def __init__(
        self,
        server_id: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.server_id: str = server_id
        self.command: str = command
        self.args: list[str] = args or []
        self.env: dict[str, str] = env or {}


class MCPToolInfo:
    """Metadata about a single tool exposed by an MCP server.

    Args:
        name: Tool name as reported by the server.
        description: Human-readable description.
        input_schema: JSON Schema describing the tool's input parameters.
    """

    def __init__(self, name: str, description: str, input_schema: dict[str, Any]) -> None:
        self.name: str = name
        self.description: str = description
        self.input_schema: dict[str, Any] = input_schema


class MCPClient:
    """Client for a single MCP server over stdio transport.

    Keeps the session alive via :class:`contextlib.AsyncExitStack` so tools can
    be called repeatedly without restarting the subprocess.

    Args:
        config: Server configuration (command, args, env).
        _session: Pre-built MCP session injected for testing.  When provided the
            subprocess is never started and ``connect`` skips straight to tool
            discovery.
    """

    def __init__(self, config: MCPServerConfig, *, _session: Any = None) -> None:
        self.config: MCPServerConfig = config
        self._session_stack: AsyncExitStack | None = None
        self._session: Any = _session
        self._tools: list[MCPToolInfo] = []

    async def connect(self) -> None:
        """Connect to the MCP server and discover its tools.

        If ``_session`` was injected at construction time the subprocess step is
        skipped and only tool discovery runs.

        Raises:
            Exception: Any error raised during subprocess startup or initialisation.
        """
        if self._session is None:
            # Lazy import — mcp is an optional runtime dependency
            from mcp import ClientSession  # type: ignore[import-not-found]
            from mcp.client.stdio import StdioServerParameters, stdio_client  # type: ignore[import-not-found]

            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env or None,
            )
            self._session_stack = AsyncExitStack()
            read, write = await self._session_stack.enter_async_context(stdio_client(params))
            self._session = await self._session_stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()

        await self._discover_tools()

    async def _discover_tools(self) -> None:
        """Fetch the tool list from the server and cache it as :class:`MCPToolInfo` objects."""
        response = await self._session.list_tools()
        self._tools = [
            MCPToolInfo(
                name=tool.name,
                description=tool.description or "",
                input_schema=getattr(tool, "inputSchema", {}),
            )
            for tool in response.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server and return its text output.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Validated arguments to pass to the tool.

        Returns:
            Text content of all response items joined with newlines.
        """
        response = await self._session.call_tool(tool_name, arguments)
        parts: list[str] = [item.text for item in response.content if hasattr(item, "text")]
        return "\n".join(parts)

    async def disconnect(self) -> None:
        """Close the subprocess connection and release all resources."""
        if self._session_stack is not None:
            await self._session_stack.aclose()
            self._session_stack = None
        self._session = None

    @property
    def tools(self) -> list[MCPToolInfo]:
        """Return a snapshot of tools discovered from this server."""
        return list(self._tools)

    @property
    def server_id(self) -> str:
        """Return the server identifier from configuration."""
        return self.config.server_id


class MCPClientManager:
    """Manages connections to multiple MCP servers.

    Reads server definitions from a JSON config file and connects concurrently.
    Individual server failures are logged as warnings and skipped so that
    remaining servers are still available.

    Config file format::

        {
          "servers": {
            "my-server": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
              "env": {}
            }
          }
        }

    Args:
        config_path: Path to the JSON configuration file.
            Defaults to ``".hcode/mcp_config.json"``.
        _client_factory: Callable ``(config) -> MCPClient`` used to create
            client instances.  Injected in tests to avoid subprocess startup.
    """

    def __init__(
        self,
        config_path: str = _DEFAULT_CONFIG_PATH,
        *,
        _client_factory: Any = None,
    ) -> None:
        self.config_path: Path = Path(config_path)
        self._client_factory: Any = _client_factory or MCPClient
        self._clients: dict[str, MCPClient] = {}

    def _load_configs(self) -> list[MCPServerConfig]:
        """Parse the JSON config file and return server configuration objects.

        Returns:
            List of :class:`MCPServerConfig` objects, or an empty list if the
            file does not exist.
        """
        if not self.config_path.exists():
            return []

        data: dict[str, Any] = json.loads(self.config_path.read_text())
        return [
            MCPServerConfig(
                server_id=server_id,
                command=server_data["command"],
                args=server_data.get("args", []),
                env=server_data.get("env", {}),
            )
            for server_id, server_data in data.get("servers", {}).items()
        ]

    async def connect_all(self) -> None:
        """Connect to all servers defined in the config file concurrently.

        Failures for individual servers are logged and skipped.
        """
        configs = self._load_configs()

        async def _connect_one(config: MCPServerConfig) -> None:
            client = self._client_factory(config)
            try:
                await client.connect()
                self._clients[config.server_id] = client
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCPClientManager: failed to connect to %s: %s", config.server_id, exc)

        await asyncio.gather(*(_connect_one(c) for c in configs))

    async def disconnect_all(self) -> None:
        """Disconnect from all connected servers and clear the client registry."""
        await asyncio.gather(*(c.disconnect() for c in self._clients.values()))
        self._clients.clear()

    def get_all_tools(self) -> list[tuple[str, MCPToolInfo]]:
        """Return all tools across all connected servers.

        Returns:
            List of ``(server_id, tool_info)`` pairs.
        """
        result: list[tuple[str, MCPToolInfo]] = []
        for server_id, client in self._clients.items():
            for tool in client.tools:
                result.append((server_id, tool))
        return result

    def get_client(self, server_id: str) -> MCPClient | None:
        """Return the client for a given server, or ``None`` if not connected.

        Args:
            server_id: Server identifier as defined in the config file.

        Returns:
            The connected :class:`MCPClient`, or ``None``.
        """
        return self._clients.get(server_id)

    @property
    def connected_servers(self) -> list[str]:
        """Return the IDs of all currently connected servers."""
        return list(self._clients.keys())


__all__ = ["MCPClient", "MCPClientManager", "MCPServerConfig", "MCPToolInfo"]
