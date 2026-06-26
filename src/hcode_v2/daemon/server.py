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
        skills_dir: str | None = None,
        workflows_dir: str = ".hcode/workflows",
        mcp_config: str = ".hcode/mcp_config.json",
    ) -> None:
        self._mock = mock
        self._skills_dir = skills_dir
        self._workflows_dir = workflows_dir
        self._mcp_config = mcp_config
        self._running = True
        self._current_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        # Per-session agent cache, keyed by thread_id. Each entry is a dict
        # {"agent": ..., "work_dir": str} so we can detect when the user opens
        # a different folder mid-session and evict the stale agent.
        self._agents: dict[str, Any] = {}

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
            elif method == "list_sessions":        await self._handle_list_sessions(req_id)
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
        # Explicit --skills-dir override scans just that dir; otherwise return the
        # union of built-in (install-relative) + project-local skills. The daemon
        # has already os.chdir'd into work_dir, so cwd here IS work_dir and
        # default_skills_dirs() picks up <work_dir>/.hcode/skills as project-local.
        from hcode_v2.skills_path import default_skills_dirs, list_skill_names
        dirs = [self._skills_dir] if self._skills_dir is not None else default_skills_dirs()
        self.send_response(req_id, {"skills": list_skill_names(dirs)})

    async def _handle_list_workflows(self, req_id: Any) -> None:
        root = Path(self._workflows_dir)
        workflows = (sorted(p.stem for p in root.glob("*.md")) if root.is_dir() else [])
        self.send_response(req_id, {"workflows": workflows})

    async def _handle_list_sessions(self, req_id: Any) -> None:
        root = Path(".hcode/sessions")
        sessions = (sorted(p.stem for p in root.glob("*.db")) if root.is_dir() else [])
        self.send_response(req_id, {"sessions": sessions})

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
        # Single-flight: refuse a new task while one is still running rather than
        # overwriting _current_task (which would also open a same-session
        # concurrent-write path on the session db).
        if self._current_task is not None and not self._current_task.done():
            self.send_response(req_id, error={"code": -32000, "message": "A task is already running"})
            return
        task: str = params.get("task", "")
        thread_id: str = params.get("thread_id") or f"gui_{abs(hash(task))}"
        work_dir: Optional[str] = params.get("work_dir") or None
        self._current_task = asyncio.create_task(self._run_task(req_id, task, thread_id, work_dir))

    async def _handle_run_workflow_dispatch(self, req_id: Any, params: dict) -> None:
        name: str = params.get("workflow", "")
        thread_id: str = params.get("thread_id") or f"gui_wf_{abs(hash(name))}"
        self._current_task = asyncio.create_task(self._run_task(req_id, f"run workflow {name}", thread_id))

    # ── run_task — C2: astream_events + StreamingBridge ──────────────────────

    async def _run_task(self, req_id: Any, task: str, thread_id: str, work_dir: Optional[str] = None) -> None:
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
            # Build the agent once per session and reuse it for later tasks on
            # the same thread_id. persist=True routes state through the SQLite
            # checkpointer (.hcode/sessions/<thread_id>.db) so sessions survive
            # across tasks and are resumable — matching the CLI.
            # Evict the cached agent if the user opened a different folder
            # (work_dir changed) — the backend root_dir is baked in at build time.
            cached = self._agents.get(thread_id)
            if cached is not None and cached["work_dir"] != work_dir:
                cached = None
                del self._agents[thread_id]
            if cached is None:
                agent = await create_hcode_agent(
                    skills_dir=self._skills_dir,
                    workflows_dir=self._workflows_dir,
                    mcp_config=self._mcp_config,
                    session_id=thread_id,
                    persist=True,
                    work_dir=work_dir,
                )
                self._agents[thread_id] = {"agent": agent, "work_dir": work_dir}
            else:
                agent = cached["agent"]
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
        """Mock run_task that emits the FULL keyless-demo story without a live model.

        Emits the SAME event shapes the real StreamingBridge produces, so the UI
        cannot tell mock from live. The arc:
            planning -> plan -> execute (two file patches) -> LSP self-correction
            (started -> 1 type error -> fix -> clean) -> verification passed -> done

        This is what makes the W3 LSP self-correction visible in every keyless
        path (Docker mock, ``dev.py --mock``, the launcher). It does NOT touch the
        real (non-mock) ``run_task`` path above.

        Event-shape sources (kept byte-compatible with live):
          - ``lsp_verify`` lines == bridge.py ``_handle_custom_event`` output:
            ``task_update`` with step ``lsp_verify:started|errors|clean``.
          - ``file_patch`` payload == the FilePatchPayload the UI reducer consumes.
        """
        async def step(type_: str, payload: dict | None = None) -> None:
            self.emit_event(type_, payload)
            await asyncio.sleep(0)

        # ── Plan ────────────────────────────────────────────────────────────
        await step("planning_started", {"timestamp": 0})
        for token in ("Analyzing the request", " and the codebase", "...\n",
                      "Drafting a step-by-step plan."):
            await step("streaming_chunk", {"content": token, "phase": "plan"})
        plan_md = (
            f"## Plan for: {task}\n"
            "1. Add `greet()` to `src/hello.py`\n"
            "2. Add a `shout()` helper to `src/utils.py`\n"
            "3. Type-check with the language server and fix any errors\n"
            "4. Verify"
        )
        await step("plan_created", {
            "markdown": plan_md,
            "taskMd": task,
            "implementationPlanMd": plan_md,
            "timestamp": 0,
        })

        # ── Execute: two files ──────────────────────────────────────────────
        await step("execution_started", {"timestamp": 0})
        await step("streaming_chunk", {"content": "Writing the two files...", "phase": "execute"})

        await step("task_update", {"markdown": "**Running tool:** `write` -> `src/hello.py`", "step": "tool:write"})
        await step("task_update", {"markdown": "**Tool done:** `write`", "step": "tool_result:write"})
        await step("file_patch", {
            "path": "src/hello.py",
            "diff": '@@ -0,0 +1,3 @@\n+def greet(name: str) -> int:\n+    # returns a str but is annotated -> int\n+    return "Hello, " + name',
            "backup": "",
            "originalContent": "",
            "newContent": 'def greet(name: str) -> int:\n    # returns a str but is annotated -> int\n    return "Hello, " + name\n',
        })

        await step("task_update", {"markdown": "**Running tool:** `write` -> `src/utils.py`", "step": "tool:write"})
        await step("task_update", {"markdown": "**Tool done:** `write`", "step": "tool_result:write"})
        await step("file_patch", {
            "path": "src/utils.py",
            "diff": '@@ -0,0 +1,2 @@\n+def shout(text: str) -> str:\n+    return text.upper() + "!"',
            "backup": "",
            "originalContent": "",
            "newContent": 'def shout(text: str) -> str:\n    return text.upper() + "!"\n',
        })

        # ── Verify with the language server: error -> fix -> clean ──────────
        await step("verification_started", {"timestamp": 0})
        await step("task_update", {
            "markdown": "**Verifying with language server** (2 files)...",
            "step": "lsp_verify:started",
        })
        await step("task_update", {
            "markdown": "**Language server found 1 error** - `src/hello.py:3` "
                        "Incompatible return type: expected `int`, got `str`. Looping back to fix.",
            "step": "lsp_verify:errors",
        })
        await step("task_update", {"markdown": "**Running tool:** `edit` -> `src/hello.py`", "step": "tool:edit"})
        await step("task_update", {"markdown": "**Tool done:** `edit`", "step": "tool_result:edit"})
        # Re-propose the SAME path with corrected content (exercises diff dedupe).
        await step("file_patch", {
            "path": "src/hello.py",
            "diff": '@@ -1,3 +1,2 @@\n-def greet(name: str) -> int:\n-    # returns a str but is annotated -> int\n-    return "Hello, " + name\n+def greet(name: str) -> str:\n+    return f"Hello, {name}"',
            "backup": "",
            "originalContent": 'def greet(name: str) -> int:\n    # returns a str but is annotated -> int\n    return "Hello, " + name\n',
            "newContent": 'def greet(name: str) -> str:\n    return f"Hello, {name}"\n',
        })
        await step("task_update", {
            "markdown": "**Language server check passed** - no errors.",
            "step": "lsp_verify:clean",
        })

        # ── Done ────────────────────────────────────────────────────────────
        await step("streaming_chunk", {"content": "All checks pass. ", "phase": "verify"})
        await step("verification", {
            "markdown": f"Task complete: {task}. 2 files changed, type-checked clean.",
            "passed": True,
            "testResults": "2 files - 0 type errors",
        })
        await step("done", {"summary": f"[mock] completed: {task}", "timestamp": 0})

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
