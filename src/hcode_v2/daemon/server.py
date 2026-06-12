"""HCode v2 JSON-RPC daemon — stdio transport.

Protocol
--------
Both JSON-RPC 2.0 responses and HcodeMessage event notifications are written
as single-line JSON objects on stdout (one object per line).

JSON-RPC response  (reply to a specific request):
  {"jsonrpc": "2.0", "id": <req_id>, "result": {...}}
  {"jsonrpc": "2.0", "id": <req_id>, "error":  {"code": ..., "message": "..."}}

HcodeMessage event (unsolicited notification, no id):
  {"type": "ready"}
  {"type": "streaming_chunk", "payload": {"content": "...", "phase": "plan"}}
  {"type": "task_update",     "payload": {"markdown": "...", "step": "..."}}
  {"type": "done",            "payload": {"summary": "...", "timestamp": ...}}

The client distinguishes them by the presence of a "jsonrpc" field.
stdout is reserved for JSON; all Python logging goes to stderr.

C2 change vs C1
---------------
_run_task now drives astream_events through StreamingBridge, replacing the
single ainvoke call.  Mock mode emits realistic streaming_chunk + task_update
events so the UI can be exercised without a live model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class JsonRpcDaemon:
    """JSON-RPC 2.0 daemon wrapping the HCode v2 DeepAgents agent.

    One instance per process.  All JSON output uses *_real_stdout* so that
    any accidental print() calls inside the agent go to stderr instead.
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

        self._real_stdout = sys.stdout
        sys.stdout = sys.stderr

    # ── Wire I/O ──────────────────────────────────────────────────────────────

    def _write(self, obj: dict) -> None:
        self._real_stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        self._real_stdout.flush()

    def send_response(self, req_id: Any, result: Any = None, error: Optional[dict] = None) -> None:
        resp: dict = {"jsonrpc": "2.0", "id": req_id}
        resp["error" if error is not None else "result"] = error if error is not None else result
        self._write(resp)

    def emit_event(self, type_: str, payload: Optional[dict] = None) -> None:
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
            if   method == "health":               await self._handle_health(req_id)
            elif method == "run_task":             await self._handle_run_task_dispatch(req_id, params)
            elif method == "run_workflow":         await self._handle_run_workflow_dispatch(req_id, params)
            elif method == "list_skills":          await self._handle_list_skills(req_id)
            elif method == "list_workflows":       await self._handle_list_workflows(req_id)
            elif method == "list_mcp_servers":     await self._handle_list_mcp_servers(req_id)
            elif method == "connect_mcp_server":   await self._handle_connect_mcp_server(req_id, params)
            elif method == "disconnect_mcp_server": await self._handle_disconnect_mcp_server(req_id, params)
            elif method == "shutdown":             await self._handle_shutdown(req_id)
            else:
                self.send_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", method, exc)
            self.send_response(req_id, error={"code": -32000, "message": str(exc)})

    # ── Synchronous method handlers ───────────────────────────────────────────

    async def _handle_health(self, req_id: Any) -> None:
        self.send_response(req_id, {"status": "running", "mock": self._mock})

    async def _handle_list_skills(self, req_id: Any) -> None:
        root = Path(self._skills_dir)
        skills = (
            sorted(d.name for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists())
            if root.is_dir() else []
        )
        self.send_response(req_id, {"skills": skills})

    async def _handle_list_workflows(self, req_id: Any) -> None:
        root = Path(self._workflows_dir)
        workflows = (sorted(p.stem for p in root.glob("*.md")) if root.is_dir() else [])
        self.send_response(req_id, {"workflows": workflows})

    async def _handle_list_mcp_servers(self, req_id: Any) -> None:
        from deepagents.mcp.client import MCPClientManager
        self.send_response(req_id, {"servers": MCPClientManager(config_path=self._mcp_config).list_known_servers()})

    async def _handle_connect_mcp_server(self, req_id: Any, params: dict) -> None:
        from deepagents.mcp.client import MCPClientManager
        name: str = params.get("server", "")
        mgr = MCPClientManager(config_path=self._mcp_config)
        known = mgr.get_known_server(name)
        if known is None:
            self.send_response(req_id, error={"code": -32602, "message": f"Unknown MCP server: {name!r}"})
            return
        mgr.add_server_to_config(name, known)
        self.send_response(req_id, {"status": "connected", "server": name})

    async def _handle_disconnect_mcp_server(self, req_id: Any, params: dict) -> None:
        name: str = params.get("server", "")
        cfg = Path(self._mcp_config)
        if not cfg.exists():
            self.send_response(req_id, error={"code": -32602, "message": "No MCP config file found."})
            return
        try:
            data = json.loads(cfg.read_text())
            servers = data.get("servers", {})
            if name not in servers:
                self.send_response(req_id, error={"code": -32602, "message": f"Server {name!r} not in config."})
                return
            del servers[name]
            data["servers"] = servers
            cfg.write_text(json.dumps(data, indent=2))
            self.send_response(req_id, {"status": "disconnected", "server": name})
        except Exception as exc:
            self.send_response(req_id, error={"code": -32000, "message": str(exc)})

    async def _handle_shutdown(self, req_id: Any) -> None:
        self._running = False
        self.send_response(req_id, {"status": "shutting_down"})

    # ── Async task dispatch ───────────────────────────────────────────────────

    async def _handle_run_task_dispatch(self, req_id: Any, params: dict) -> None:
        task: str = params.get("task", "")
        thread_id: str = params.get("thread_id") or f"daemon_{abs(hash(task))}"
        self._current_task = asyncio.create_task(self._run_task(req_id, task, thread_id))

    async def _handle_run_workflow_dispatch(self, req_id: Any, params: dict) -> None:
        name: str = params.get("workflow", "")
        thread_id: str = params.get("thread_id") or f"daemon_wf_{abs(hash(name))}"
        self._current_task = asyncio.create_task(self._run_task(req_id, f"run workflow {name}", thread_id))

    # ── run_task — C2: astream_events + StreamingBridge ──────────────────────

    async def _run_task(self, req_id: Any, task: str, thread_id: str) -> None:
        """Execute one task, streaming events to the client via StreamingBridge."""
        self.send_response(req_id, {"status": "started", "thread_id": thread_id})

        if self._mock:
            await self._mock_streaming_task(task)
            return

        from langchain_core.messages import HumanMessage

        from hcode_v2.agent.factory import create_hcode_agent
        from hcode_v2.daemon.bridge import StreamingBridge

        bridge = StreamingBridge(emit_fn=self.emit_event, pev_mode=True)
        bridge.on_task_start()

        try:
            agent = await create_hcode_agent(
                skills_dir=self._skills_dir,
                workflows_dir=self._workflows_dir,
                mcp_config=self._mcp_config,
                session_id=thread_id,
                persist=False,
            )
            last_text = ""
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=task)]},
                # recursion_limit MUST be explicit on the astream_events path:
                # langchain_core stamps its default (25) into the config, which
                # overrides the agent's bound 9999 and kills tasks after ~5 tool
                # rounds. Mirrors the CLI fix (cli/main.py:242). Matters more now
                # that PEV's execute phase runs up to 15 rounds.
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 1000,
                },
                version="v2",
            ):
                bridge.process_event(event)
                # Track last AI message content for the done summary
                if event.get("event") == "on_chat_model_end":
                    msg = event.get("data", {}).get("output", {})
                    if hasattr(msg, "content"):
                        last_text = msg.content if isinstance(msg.content, str) else ""

            bridge.finalize(summary=last_text)

        except Exception as exc:
            logger.error("run_task streaming failed: %s", exc)
            self.emit_event("error", {"message": str(exc), "recoverable": False})

    async def _mock_streaming_task(self, task: str) -> None:
        """Mock run_task that emits realistic streaming events without a live model."""
        await asyncio.sleep(0)
        self.emit_event("planning_started", {"timestamp": 0})

        # Emit token stream simulating a plan
        plan_tokens = ["I ", "will ", "complete: ", task, " "]
        for token in plan_tokens:
            self.emit_event("streaming_chunk", {"content": token, "phase": "plan"})
            await asyncio.sleep(0)

        # Emit PLAN COMPLETE marker (triggers phase transition in a real bridge)
        self.emit_event("streaming_chunk", {"content": "PLAN COMPLETE", "phase": "plan"})
        await asyncio.sleep(0)
        self.emit_event("plan_created", {
            "markdown": f"I will complete: {task} PLAN COMPLETE",
            "taskMd": task,
            "implementationPlanMd": f"I will complete: {task} PLAN COMPLETE",
            "timestamp": 0,
        })
        self.emit_event("execution_started", {"timestamp": 0})

        # Simulate a tool call
        self.emit_event("task_update", {
            "markdown": "**Running tool:** `echo`",
            "step": "tool:echo",
        })
        await asyncio.sleep(0)
        self.emit_event("task_update", {
            "markdown": "**Tool done:** `echo`",
            "step": "tool_result:echo",
        })
        await asyncio.sleep(0)

        # Execution done
        self.emit_event("streaming_chunk", {"content": "EXECUTION COMPLETE", "phase": "execute"})
        await asyncio.sleep(0)
        self.emit_event("verification_started", {"timestamp": 0})
        await asyncio.sleep(0)

        self.emit_event("done", {"summary": f"[mock] completed: {task}", "timestamp": 0})

    # ── Main read loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        self.emit_event("ready")
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                line: str = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception as exc:
                logger.error("stdin read error: %s", exc)
                break
            if not line:
                break
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
