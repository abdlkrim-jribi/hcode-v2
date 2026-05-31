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

from hcode_v2.provider.fallback import maybe_wrap
from hcode_v2.utils.config import Config

logger = logging.getLogger(__name__)


def _build_model():
    """Build the LangChain chat model from environment config.

    Model identity, endpoint, and tool-calling strategy come from
    ``Config.from_env()``, which resolves:

    - model name: ``HCODE_MODEL_NAME`` → ``HCODE_MODEL`` → ``"gpt-4o-mini"``
    - api_key:    ``HCODE_MODEL_API_KEY`` → ``OPENAI_API_KEY``
    - base_url:   ``HCODE_MODEL_BASE_URL`` → ``OPENAI_BASE_URL``
    - mode:       ``HCODE_TOOLCALL_MODE``  → ``"native"`` (default)

    ``ANTHROPIC_API_KEY`` selects ``ChatAnthropic`` when set and no
    OpenAI-compatible key is resolved.  ``HCODE_MAX_TOKENS`` caps output
    tokens (default 2000).

    When ``HCODE_TOOLCALL_MODE`` is ``"json"`` or ``"auto"``, the model is
    wrapped with ``JsonToolCallWrapper`` so the agent degrades gracefully on
    endpoints that lack native function-calling support.  Run
    ``scripts/probe_model.py`` against the endpoint to determine the right mode.
    """
    config = Config.from_env()
    max_tokens = int(os.getenv("HCODE_MAX_TOKENS", "2000"))
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if anthropic_key and not config.api_key:
        from langchain_anthropic import ChatAnthropic
        base_model = ChatAnthropic(
            model=config.model,
            max_tokens=max_tokens,
            api_key=anthropic_key,
        )
    else:
        from langchain_openai import ChatOpenAI
        base_model = ChatOpenAI(
            model=config.model,
            max_tokens=max_tokens,
            api_key=config.api_key,
            base_url=config.base_url,
        )

    return maybe_wrap(base_model, config.toolcall_mode)


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
