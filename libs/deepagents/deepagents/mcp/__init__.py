"""MCP (Model Context Protocol) client package for deepagents."""

from deepagents.mcp.bridge import MCPToolBridge, MCPToolRegistry
from deepagents.mcp.client import MCPClient, MCPClientManager, MCPServerConfig, MCPToolInfo

__all__ = [
    "MCPClient",
    "MCPClientManager",
    "MCPServerConfig",
    "MCPToolBridge",
    "MCPToolInfo",
    "MCPToolRegistry",
]
