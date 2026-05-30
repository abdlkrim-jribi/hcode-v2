"""HCode v2 agent factory."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware
from deepagents.middleware.pev import PEVMiddleware
from deepagents.middleware.safety_guard import SafetyGuardMiddleware
from deepagents.middleware.workflows import WorkflowMiddleware
from deepagents.mcp.bridge import MCPToolRegistry
from deepagents.mcp.client import MCPClientManager

logger = logging.getLogger(__name__)


def _build_model():
    """Build LangChain chat model from environment variables.

    Reads:
      OPENAI_API_KEY      - API key (works for OpenRouter too)
      OPENAI_BASE_URL     - optional base URL (set for OpenRouter)
      ANTHROPIC_API_KEY   - Anthropic key (if using Anthropic)
      HCODE_MODEL         - model string, e.g. gpt-4o-mini or claude-haiku-4-5
      HCODE_MAX_TOKENS    - optional, default 2000
    """
    model_name = os.getenv("HCODE_MODEL", "gpt-4o-mini")
    max_tokens = int(os.getenv("HCODE_MAX_TOKENS", "2000"))

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")

    if anthropic_key and not openai_key:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model_name,
            max_tokens=max_tokens,
            api_key=anthropic_key,
        )

    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model_name,
        max_tokens=max_tokens,
        api_key=openai_key,
        base_url=base_url,
    )


async def create_hcode_agent(
    skills_dir: str = ".hcode/skills",
    workflows_dir: str = ".hcode/workflows",
    mcp_config: str = ".hcode/mcp_config.json",
    enable_pev: bool = True,
    enable_safety: bool = True,
    session_id: str = "default",
    persist: bool = True,
):
    """Assemble the full HCode v2 agent from environment config."""
    from deepagents.checkpointers.sqlite import HCodeSQLiteCheckpointer
    from langgraph.checkpoint.memory import MemorySaver

    if persist:
        Path(".hcode/sessions").mkdir(parents=True, exist_ok=True)
        checkpointer = HCodeSQLiteCheckpointer(
            db_path=f".hcode/sessions/{session_id}.db"
        )
    else:
        checkpointer = MemorySaver()

    model = _build_model()

    # Create backend ONCE — shared by SummarizationMiddleware and agent
    backend = LocalShellBackend(virtual_mode=False)

    middleware = []
    if enable_pev:
        middleware.append(PEVMiddleware())
    if enable_safety:
        middleware.append(SafetyGuardMiddleware())
    middleware.append(HCodeSkillsMiddleware(skills_dir=skills_dir))
    middleware.append(WorkflowMiddleware(workflows_dir=workflows_dir))

    mcp_tools = []
    manager = MCPClientManager(config_path=mcp_config)
    if manager.is_configured:
        try:
            mcp_tools = await MCPToolRegistry.build_tools(manager)
        except Exception as e:
            logger.warning("MCP failed: %s", e)

    # All 31 tools are ~1213 tokens total — safe to pass all
    from hcode_v2.tools.registry import get_all_tools
    all_tools = get_all_tools() + mcp_tools

    return create_deep_agent(
        model=model,
        tools=all_tools,
        middleware=middleware,
        backend=backend,
        checkpointer=checkpointer,
    )
