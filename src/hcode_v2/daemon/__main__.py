"""Entry point for ``python -m hcode_v2.daemon``.

Usage:
    python -m hcode_v2.daemon                  # real agent (needs model config)
    python -m hcode_v2.daemon --mock           # deterministic mock responses
    python -m hcode_v2.daemon --work-dir /p    # set working directory
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Configure logging before importing anything else so all modules
# that call getLogger() at import time inherit this config.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hcode_v2.daemon",
        description="HCode v2 JSON-RPC daemon (stdio transport)",
    )
    parser.add_argument("--mock", action="store_true", help="Use mock agent (no LLM calls)")
    parser.add_argument("--work-dir", default=None, metavar="DIR", help="Set working directory")
    parser.add_argument(
        "--skills-dir",
        default=".hcode/skills",
        metavar="DIR",
        help="Skills root (default: .hcode/skills)",
    )
    parser.add_argument(
        "--workflows-dir",
        default=".hcode/workflows",
        metavar="DIR",
        help="Workflows root (default: .hcode/workflows)",
    )
    parser.add_argument(
        "--mcp-config",
        default=".hcode/mcp_config.json",
        metavar="FILE",
        help="MCP config file (default: .hcode/mcp_config.json)",
    )
    args = parser.parse_args()

    if args.work_dir:
        os.chdir(args.work_dir)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from hcode_v2.daemon.server import JsonRpcDaemon

    daemon = JsonRpcDaemon(
        mock=args.mock,
        skills_dir=args.skills_dir,
        workflows_dir=args.workflows_dir,
        mcp_config=args.mcp_config,
    )
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
