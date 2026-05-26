"""MCP tool bridge — wraps MCP server tools as LangChain BaseTool instances."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool
from pydantic import Field, create_model

if TYPE_CHECKING:
    from deepagents.mcp.client import MCPClient, MCPClientManager, MCPToolInfo

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_args_schema(input_schema: dict[str, Any]) -> type:
    """Build a Pydantic model from a JSON Schema ``input_schema`` dict.

    Required properties become mandatory fields; optional properties default to
    ``None``.  Unknown JSON types fall back to ``Any``.

    Args:
        input_schema: JSON Schema object with optional ``properties`` and
            ``required`` keys.

    Returns:
        A dynamically created Pydantic model class.
    """
    properties: dict[str, Any] = input_schema.get("properties", {})
    required: set[str] = set(input_schema.get("required", []))
    fields: dict[str, Any] = {}

    for prop_name, prop_schema in properties.items():
        json_type = prop_schema.get("type", "string")
        python_type: type = _JSON_TYPE_MAP.get(json_type, Any)  # type: ignore[assignment]
        description: str = prop_schema.get("description", "")

        if prop_name in required:
            fields[prop_name] = (python_type, Field(description=description))
        else:
            fields[prop_name] = (python_type | None, Field(default=None, description=description))

    return create_model("MCPToolArgs", **fields)


class MCPToolBridge(BaseTool):
    """LangChain :class:`~langchain_core.tools.BaseTool` that executes an MCP server tool.

    The tool name is formatted as ``mcp_{server_id}_{tool_name}`` to avoid
    collisions when multiple MCP servers are connected simultaneously.

    Args:
        name: Unique tool name exposed to the LLM.
        description: Tool description forwarded from the MCP server.
        args_schema: Pydantic model for input validation, built from the
            server's JSON Schema.
        mcp_client: Connected :class:`~deepagents.mcp.client.MCPClient` to
            use for execution.
        mcp_tool_name: Original tool name on the MCP server (may differ from
            ``name`` which includes the server prefix).
    """

    name: str = ""
    description: str = ""
    mcp_client: Any = None
    mcp_tool_name: str = ""

    def _run(self, **kwargs: Any) -> str:
        """Execute the MCP tool synchronously via :func:`asyncio.run`.

        Args:
            **kwargs: Validated tool arguments.

        Returns:
            Tool output as a string.
        """
        return asyncio.run(self._arun(**kwargs))

    async def _arun(self, **kwargs: Any) -> str:
        """Execute the MCP tool asynchronously.

        Args:
            **kwargs: Validated tool arguments.

        Returns:
            Tool output as a string.
        """
        return await self.mcp_client.call_tool(self.mcp_tool_name, kwargs)


class MCPToolRegistry:
    """Builds :class:`MCPToolBridge` instances for all tools in a connected manager.

    Args:
        manager: An :class:`~deepagents.mcp.client.MCPClientManager` with at
            least one active connection.
    """

    def __init__(self, manager: MCPClientManager) -> None:
        self._manager: MCPClientManager = manager

    @staticmethod
    def build_tools(manager: MCPClientManager) -> list[MCPToolBridge]:
        """Create :class:`MCPToolBridge` instances for every tool in the manager.

        Args:
            manager: Connected manager exposing server tools.

        Returns:
            List of ready-to-use :class:`MCPToolBridge` instances.
        """
        tools: list[MCPToolBridge] = []
        for server_id, tool_info in manager.get_all_tools():
            client = manager.get_client(server_id)
            if client is None:
                continue
            tools.append(
                MCPToolBridge(
                    name=f"mcp_{server_id}_{tool_info.name}",
                    description=tool_info.description,
                    args_schema=_build_args_schema(tool_info.input_schema),
                    mcp_client=client,
                    mcp_tool_name=tool_info.name,
                )
            )
        return tools

    def get_tools(self) -> list[MCPToolBridge]:
        """Return bridges built from the manager passed at construction time.

        Returns:
            List of :class:`MCPToolBridge` instances.
        """
        return self.build_tools(self._manager)


__all__ = ["MCPToolBridge", "MCPToolRegistry"]
