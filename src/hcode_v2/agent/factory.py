"""HCode v2 agent factory — assembles the full middleware stack and returns a ready agent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware
from deepagents.middleware.pev import PEVMiddleware
from deepagents.middleware.safety_guard import SafetyGuardMiddleware
from deepagents.middleware.workflows import WorkflowMiddleware
from deepagents.mcp.bridge import MCPToolRegistry
from deepagents.mcp.client import MCPClientManager
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


async def create_hcode_agent(
    model: str = "openai:gpt-4o",
    skills_dir: str = ".hcode/skills",
    workflows_dir: str = ".hcode/workflows",
    mcp_config: str = ".hcode/mcp_config.json",
    enable_pev: bool = True,
    enable_safety: bool = True,
) -> CompiledStateGraph:
    """Assemble and return a fully configured HCode v2 agent.

    Builds the middleware stack (PEV, SafetyGuard, HCode skills, workflows),
    connects any configured MCP servers, and calls :func:`~deepagents.create_deep_agent`
    with the resulting configuration.

    Args:
        model: LangChain model string (e.g. ``"openai:gpt-4o"`` or
            ``"anthropic:claude-sonnet-4-6"``).
        skills_dir: Path to the HCode skills directory.
        workflows_dir: Path to the HCode workflows directory.
        mcp_config: Path to the MCP server configuration JSON file.
        enable_pev: When ``True`` the Plan → Execute → Verify middleware is active.
        enable_safety: When ``True`` the SafetyGuard file-backup middleware is active.

    Returns:
        A compiled LangGraph ``CompiledStateGraph`` ready for invocation.
    """
    middleware: list[Any] = []
    if enable_pev:
        middleware.append(PEVMiddleware())
    if enable_safety:
        middleware.append(SafetyGuardMiddleware())
    middleware.append(HCodeSkillsMiddleware(skills_dir=skills_dir))
    middleware.append(WorkflowMiddleware(workflows_dir=workflows_dir))

    mcp_tools: list[Any] = []
    if Path(mcp_config).exists():
        manager = MCPClientManager(config_path=mcp_config)
        try:
            await manager.connect_all()
            mcp_tools = MCPToolRegistry.build_tools(manager)
            if mcp_tools:
                logger.info("Loaded %d MCP tool(s) from %s", len(mcp_tools), mcp_config)
        except Exception:
            logger.warning("MCP connection failed — continuing without MCP tools", exc_info=True)

    return create_deep_agent(
        model,
        tools=mcp_tools or None,
        middleware=middleware,
        backend=LocalShellBackend(),
    )
