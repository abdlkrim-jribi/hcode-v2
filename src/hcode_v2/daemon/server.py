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
        mcp_client_factory: Any = None,
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
        # Phase 1 live MCP: the daemon owns real connections so the panel can
        # report actual status + tool counts (not an optimistic toggle). These
        # are SEPARATE from the per-agent manager the factory builds at agent
        # build time — connect here proves the server works and surfaces its
        # tools; the agent gets its own tools when rebuilt (cache eviction below).
        # _mcp_client_factory is injectable so tests need no real subprocess.
        self._mcp_clients: dict[str, Any] = {}   # server_id -> connected MCPClient
        self._mcp_errors: dict[str, str] = {}    # server_id -> last connect error
        self._mcp_client_factory = mcp_client_factory

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

    # ── MCP: live connection management (Phase 1, no-auth servers) ─────────────
    #
    # The daemon owns real MCP connections so the panel reflects genuine state:
    #   - connect spawns the server subprocess, runs the init handshake, lists
    #     tools, and reports the live tool count (or a real error).
    #   - list returns the catalog ∪ configured servers, each annotated with its
    #     real status (connected / error / needs-auth / disconnected) + tool count.
    #   - disconnect tears the live connection down (basic; full lifecycle = P3).
    # Token-based servers (env_required) are surfaced as "needs-auth" and NOT
    # attempted in Phase 1 unless their secret is already in os.environ (the
    # existing CLI/.env path, preserved). Secure token entry is Phase 2.

    def _resolve_mcp_factory(self) -> Any:
        """Return the MCP client factory (test-injected, else env-injecting)."""
        if self._mcp_client_factory is not None:
            return self._mcp_client_factory
        # HCode-side factory (NOT vendored): injects each server's env_required
        # secrets from os.environ and strips None-valued tool args. Same factory
        # the agent uses, so the daemon's live connection mirrors the agent's.
        from hcode_v2.agent.mcp_env import env_injecting_client_factory
        return env_injecting_client_factory

    def _server_def(self, name: str) -> dict | None:
        """Resolve a server definition from the KNOWN_SERVERS catalog or config."""
        from deepagents.mcp.client import KNOWN_SERVERS
        if name in KNOWN_SERVERS:
            return KNOWN_SERVERS[name]
        return self._configured_servers().get(name)

    def _configured_servers(self) -> dict[str, dict]:
        """Return the servers block from the on-disk MCP config ({} if none)."""
        cfg = Path(self._mcp_config)
        if not cfg.exists():
            return {}
        try:
            return json.loads(cfg.read_text()).get("servers", {})
        except Exception:
            return {}

    def _missing_required_env(self, name: str, server_def: dict) -> list[str]:
        """Required env vars NOT satisfied by os.environ or the config env block.

        A non-empty result means the server needs a token we don't have →
        "needs-auth" (Phase 2). An empty result means we can connect in Phase 1.
        """
        import os
        from deepagents.mcp.client import KNOWN_SERVERS
        required = (KNOWN_SERVERS.get(name) or server_def or {}).get("env_required", [])
        env_block = (server_def or {}).get("env", {})
        return [v for v in required if v not in os.environ and v not in env_block]

    async def _connect_live(self, name: str, server_def: dict) -> Any:
        """Spawn + connect a live MCP client for one server; cache it. Returns it."""
        from deepagents.mcp.client import MCPServerConfig
        factory = self._resolve_mcp_factory()
        client = factory(MCPServerConfig(
            name,
            server_def["command"],
            server_def.get("args", []),
            server_def.get("env", {}),
        ))
        await client.connect()
        self._mcp_clients[name] = client
        return client

    def _server_status(self, name: str, server_def: dict, configured: bool) -> dict:
        """Build one server's status row for list_mcp_servers."""
        if name in self._mcp_clients:
            tools = [t.name for t in self._mcp_clients[name].tools]
            return {"status": "connected", "toolCount": len(tools), "tools": tools}
        if name in self._mcp_errors:
            return {"status": "error", "toolCount": 0, "errorMessage": self._mcp_errors[name]}
        if self._missing_required_env(name, server_def):
            return {"status": "needs-auth", "toolCount": 0}
        # Not connected: "configured" if it's in the config file, else available.
        return {"status": "disconnected", "toolCount": 0}

    async def _handle_list_mcp_servers(self, req_id: Any) -> None:
        from deepagents.mcp.client import KNOWN_SERVERS
        configured = self._configured_servers()
        # Catalog ∪ configured (config wins for command/args/env/description).
        names = list(dict.fromkeys([*KNOWN_SERVERS.keys(), *configured.keys()]))
        servers: list[dict] = []
        for name in names:
            defn = {**(KNOWN_SERVERS.get(name) or {}), **configured.get(name, {})}
            row = {
                "name": name,
                "description": defn.get("description", ""),
                "configured": name in configured,
                **self._server_status(name, defn, name in configured),
            }
            servers.append(row)
        self.send_response(req_id, {"servers": servers})

    async def _handle_connect_mcp_server(self, req_id: Any, params: dict) -> None:
        name: str = params.get("server", "")
        server_def = self._server_def(name)
        if server_def is None:
            self.send_response(req_id, error={"code": -32602, "message": f"Unknown MCP server: {name!r}"})
            return

        # Phase 1 is no-auth only: a server needing a token we don't have is
        # surfaced as needs-auth and NOT spawned (secure token entry = Phase 2).
        missing = self._missing_required_env(name, server_def)
        if missing:
            self.send_response(req_id, {
                "status": "needs-auth", "server": name,
                "message": f"Requires {', '.join(missing)} — secure auth lands in Phase 2.",
            })
            return

        # Persist to config FIRST so the agent picks the server up on its next
        # build (cache eviction keys on the config signature). Only KNOWN presets
        # are written here; already-configured custom servers are left as-is.
        from deepagents.mcp.client import KNOWN_SERVERS, MCPClientManager
        if name in KNOWN_SERVERS:
            MCPClientManager(config_path=self._mcp_config).add_server_to_config(name, KNOWN_SERVERS[name])

        # Actually connect: real subprocess + init handshake + list_tools.
        try:
            client = await self._connect_live(name, server_def)
        except Exception as exc:  # noqa: BLE001
            self._mcp_errors[name] = str(exc)
            self._mcp_clients.pop(name, None)
            self.send_response(req_id, error={"code": -32000, "message": str(exc)})
            return

        self._mcp_errors.pop(name, None)
        tools = [t.name for t in client.tools]
        self.send_response(req_id, {
            "status": "connected", "server": name,
            "toolCount": len(tools), "tools": tools,
        })

    async def _handle_disconnect_mcp_server(self, req_id: Any, params: dict) -> None:
        name: str = params.get("server", "")
        # Tear down the live connection if we have one (best-effort).
        client = self._mcp_clients.pop(name, None)
        torn_down = client is not None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP disconnect of %s failed: %s", name, exc)
        self._mcp_errors.pop(name, None)

        # Remove from the on-disk config so the agent drops it on next build.
        removed = False
        cfg = Path(self._mcp_config)
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text())
                servers = data.get("servers", {})
                if name in servers:
                    del servers[name]
                    data["servers"] = servers
                    cfg.write_text(json.dumps(data, indent=2))
                    removed = True
            except Exception as exc:  # noqa: BLE001
                self.send_response(req_id, error={"code": -32000, "message": str(exc)})
                return

        if not torn_down and not removed:
            self.send_response(req_id, error={"code": -32602, "message": f"Server {name!r} is not connected or configured."})
            return
        self.send_response(req_id, {"status": "disconnected", "server": name})

    def _mcp_config_signature(self) -> frozenset[str]:
        """Signature of the configured MCP servers, for agent-cache eviction.

        The factory connects every server in the config at agent build time, so
        the set of configured server names is exactly what determines which MCP
        tools an agent ends up with. Connect adds a name, disconnect removes one;
        either changes this signature → the cached agent evicts and rebuilds on
        the next task → the new tools actually reach it. No MCP change → identical
        signature → no eviction (zero regression for the no-MCP path).
        """
        return frozenset(self._configured_servers().keys())

    async def _handle_shutdown(self, req_id: Any) -> None:
        self._running = False
        # Best-effort teardown of any live MCP subprocesses on shutdown.
        for name, client in list(self._mcp_clients.items()):
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP shutdown disconnect of %s failed: %s", name, exc)
        self._mcp_clients.clear()
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
        # active_skills: list of skill names the agent should load, or None = all.
        # Validate: must be a non-empty list of strings; anything else → None (all).
        raw_skills = params.get("active_skills")
        active_skills: Optional[list[str]] = (
            [s for s in raw_skills if isinstance(s, str)] or None
            if isinstance(raw_skills, list) else None
        )
        self._current_task = asyncio.create_task(
            self._run_task(req_id, task, thread_id, work_dir, active_skills)
        )

    async def _handle_run_workflow_dispatch(self, req_id: Any, params: dict) -> None:
        name: str = params.get("workflow", "")
        thread_id: str = params.get("thread_id") or f"gui_wf_{abs(hash(name))}"
        self._current_task = asyncio.create_task(self._run_task(req_id, f"run workflow {name}", thread_id))

    # ── run_task — C2: astream_events + StreamingBridge ──────────────────────

    async def _run_task(self, req_id: Any, task: str, thread_id: str, work_dir: Optional[str] = None, active_skills: Optional[list[str]] = None) -> None:
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
            # Evict the cached agent when work_dir, active_skills, OR the MCP
            # config changes — all three are baked into the agent at build time
            # (backend root_dir, the skills middleware allowlist, and the set of
            # MCP servers whose tools the factory wires in, respectively). The
            # MCP key makes a connect/disconnect actually reach the running agent
            # on the next task instead of silently no-op'ing on a cached agent.
            skills_key = frozenset(active_skills) if active_skills else None
            mcp_key = self._mcp_config_signature()
            cached = self._agents.get(thread_id)
            if cached is not None and (
                cached["work_dir"] != work_dir
                or cached["skills_key"] != skills_key
                or cached["mcp_key"] != mcp_key
            ):
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
                    active_skills=list(skills_key) if skills_key else None,
                )
                self._agents[thread_id] = {
                    "agent": agent, "work_dir": work_dir,
                    "skills_key": skills_key, "mcp_key": mcp_key,
                }
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
