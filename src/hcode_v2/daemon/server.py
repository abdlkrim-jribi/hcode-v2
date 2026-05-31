"""HCode v2 JSON-RPC daemon — stdio transport.

Protocol
--------
Both JSON-RPC 2.0 responses and HcodeMessage event notifications are written
as single-line JSON objects on stdout (one object per line).

JSON-RPC response (reply to a specific request):
  {"jsonrpc": "2.0", "id": <req_id>, "result": {...}}
  {"jsonrpc": "2.0", "id": <req_id>, "error": {"code": ..., "message": "..."}}

HcodeMessage event notification (unsolicited, no id):
  {"type": "ready"}
  {"type": "done", "payload": {"result": "..."}}
  {"type": "error", "payload": {"message": "..."}}

The client distinguishes them by the presence of a "jsonrpc" field.

stdout is reserved for JSON. All Python logging goes to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class JsonRpcDaemon:
    """JSON-RPC 2.0 daemon that wraps the HCode v2 DeepAgents agent.

    C1 note: run_task launches the agent synchronously (awaits result), then
    emits a "done" event. Streaming (C2) will replace this with astream_events.
    """

    def __init__(
        self,
        mock: bool = False,
        skills_dir: str = ".hcode/skills",
        workflows_dir: str = ".hcode/workflows",
        mcp_config: str = ".hcode/mcp_config.json",
    ) -> None:
        self._mock = mock
        self._skills_dir = skills_dir
        self._workflows_dir = workflows_dir
        self._mcp_config = mcp_config
        self._running = True
        self._current_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

        # Reserve stdout exclusively for JSON; route any accidental prints to stderr.
        self._real_stdout = sys.stdout
        sys.stdout = sys.stderr

    # ── Output helpers ────────────────────────────────────────────────────────

    def _write(self, obj: dict) -> None:
        """Serialize *obj* as one JSON line on the real stdout."""
        line = json.dumps(obj, ensure_ascii=True)
        self._real_stdout.write(line + "\n")
        self._real_stdout.flush()

    def send_response(
        self,
        req_id: Any,
        result: Any = None,
        error: Optional[dict] = None,
    ) -> None:
        resp: dict = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            resp["error"] = error
        else:
            resp["result"] = result
        self._write(resp)

    def emit_event(self, type_: str, payload: Optional[dict] = None) -> None:
        """Emit an unsolicited HcodeMessage notification."""
        msg: dict = {"type": type_}
        if payload is not None:
            msg["payload"] = payload
        self._write(msg)

    # ── Request router ────────────────────────────────────────────────────────

    async def handle_request(self, request: dict) -> None:
        method: str = request.get("method", "")
        params: dict = request.get("params") or {}
        req_id = request.get("id")

        try:
            if method == "health":
                await self._handle_health(req_id)
            elif method == "run_task":
                await self._handle_run_task_dispatch(req_id, params)
            elif method == "run_workflow":
                await self._handle_run_workflow_dispatch(req_id, params)
            elif method == "list_skills":
                await self._handle_list_skills(req_id)
            elif method == "list_workflows":
                await self._handle_list_workflows(req_id)
            elif method == "list_mcp_servers":
                await self._handle_list_mcp_servers(req_id)
            elif method == "connect_mcp_server":
                await self._handle_connect_mcp_server(req_id, params)
            elif method == "disconnect_mcp_server":
                await self._handle_disconnect_mcp_server(req_id, params)
            elif method == "shutdown":
                await self._handle_shutdown(req_id)
            else:
                self.send_response(req_id, error={
                    "code": -32601,
                    "message": f"Method not found: {method}",
                })
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", method, exc)
            self.send_response(req_id, error={"code": -32000, "message": str(exc)})

    # ── Method handlers ───────────────────────────────────────────────────────

    async def _handle_health(self, req_id: Any) -> None:
        self.send_response(req_id, {"status": "running", "mock": self._mock})

    async def _handle_list_skills(self, req_id: Any) -> None:
        root = Path(self._skills_dir)
        skills = (
            sorted(
                d.name
                for d in root.iterdir()
                if d.is_dir() and (d / "SKILL.md").exists()
            )
            if root.is_dir()
            else []
        )
        self.send_response(req_id, {"skills": skills})

    async def _handle_list_workflows(self, req_id: Any) -> None:
        root = Path(self._workflows_dir)
        workflows = (
            sorted(p.stem for p in root.glob("*.md"))
            if root.is_dir()
            else []
        )
        self.send_response(req_id, {"workflows": workflows})

    async def _handle_list_mcp_servers(self, req_id: Any) -> None:
        from deepagents.mcp.client import MCPClientManager
        manager = MCPClientManager(config_path=self._mcp_config)
        self.send_response(req_id, {"servers": manager.list_known_servers()})

    async def _handle_connect_mcp_server(self, req_id: Any, params: dict) -> None:
        from deepagents.mcp.client import MCPClientManager
        server_name: str = params.get("server", "")
        manager = MCPClientManager(config_path=self._mcp_config)
        known = manager.get_known_server(server_name)
        if known is None:
            self.send_response(req_id, error={
                "code": -32602,
                "message": f"Unknown MCP server: {server_name!r}. "
                           "Call list_mcp_servers to see available names.",
            })
            return
        manager.add_server_to_config(server_name, known)
        self.send_response(req_id, {"status": "connected", "server": server_name})

    async def _handle_disconnect_mcp_server(self, req_id: Any, params: dict) -> None:
        server_name: str = params.get("server", "")
        config_path = Path(self._mcp_config)
        if not config_path.exists():
            self.send_response(req_id, error={
                "code": -32602,
                "message": "No MCP config file found.",
            })
            return
        try:
            data = json.loads(config_path.read_text())
            servers = data.get("servers", {})
            if server_name not in servers:
                self.send_response(req_id, error={
                    "code": -32602,
                    "message": f"Server {server_name!r} is not in the config.",
                })
                return
            del servers[server_name]
            data["servers"] = servers
            config_path.write_text(json.dumps(data, indent=2))
            self.send_response(req_id, {"status": "disconnected", "server": server_name})
        except Exception as exc:
            self.send_response(req_id, error={"code": -32000, "message": str(exc)})

    async def _handle_shutdown(self, req_id: Any) -> None:
        self._running = False
        self.send_response(req_id, {"status": "shutting_down"})

    # ── Task dispatch (async) ─────────────────────────────────────────────────

    async def _handle_run_task_dispatch(self, req_id: Any, params: dict) -> None:
        task: str = params.get("task", "")
        thread_id: str = params.get("thread_id") or f"daemon_{abs(hash(task))}"
        self._current_task = asyncio.create_task(
            self._run_task(req_id, task, thread_id)
        )

    async def _handle_run_workflow_dispatch(self, req_id: Any, params: dict) -> None:
        name: str = params.get("workflow", "")
        thread_id: str = params.get("thread_id") or f"daemon_wf_{abs(hash(name))}"
        task = f"run workflow {name}"
        self._current_task = asyncio.create_task(
            self._run_task(req_id, task, thread_id)
        )

    async def _run_task(self, req_id: Any, task: str, thread_id: str) -> None:
        """Execute one task and emit result as a "done" event."""
        self.send_response(req_id, {"status": "started", "thread_id": thread_id})

        if self._mock:
            await asyncio.sleep(0)  # yield so the response line is flushed first
            self.emit_event("done", {"result": f"[mock] completed: {task}"})
            return

        try:
            from langchain_core.messages import HumanMessage

            from hcode_v2.agent.factory import create_hcode_agent

            agent = await create_hcode_agent(
                skills_dir=self._skills_dir,
                workflows_dir=self._workflows_dir,
                mcp_config=self._mcp_config,
                session_id=thread_id,
                persist=False,
            )
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={"configurable": {"thread_id": thread_id}},
            )
            messages = result.get("messages", [])
            last = messages[-1].content if messages else ""
            if isinstance(last, list):
                last = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in last
                )
            self.emit_event("done", {"result": str(last)})
        except Exception as exc:
            logger.error("run_task failed: %s", exc)
            self.emit_event("error", {"message": str(exc)})

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Emit ready, then process stdin line-by-line until shutdown."""
        self.emit_event("ready")
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                line: str = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception as exc:
                logger.error("stdin read error: %s", exc)
                break

            if not line:
                break  # EOF

            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid JSON on stdin: %s", exc)
                continue

            await self.handle_request(request)

        logger.info("Daemon stopped.")
