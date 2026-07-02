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
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Minimum length for a value to be treated as a redactable secret. Guards against
# accidentally redacting short, common substrings (e.g. a 4-char URL fragment).
_MIN_SECRET_LEN = 8
_REDACTED = "***REDACTED***"


class _SecretRedactingFilter(logging.Filter):
    """Logging filter that scrubs registered secret values from every log record.

    The daemon routes logging to stderr; this guarantees a token can never land
    in a log line even if some library logs an env dump or an error containing it.
    Backed by the SAME live set the stdout redactor uses, so registering a secret
    once protects both streams.
    """

    def __init__(self, secrets: set[str]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            try:
                msg = record.getMessage()
                redacted = _redact_secrets(msg, self._secrets)
                if redacted != msg:
                    record.msg = redacted
                    record.args = ()
            except Exception:  # pragma: no cover - never let logging crash the daemon
                pass
        return True


def _redact_secrets(text: str, secrets: set[str]) -> str:
    """Replace every registered secret value in *text* with a redaction marker."""
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, _REDACTED)
    return text


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
        # Plan review (HITL): while a run is paused at the plan→execute boundary,
        # _run_task awaits this future; the resume_plan RPC resolves it with the
        # accept/reject decision. None when no plan is pending. Keeping the run's
        # task alive (awaiting) means Abort still cancels a paused run cleanly.
        self._plan_decision_future: Optional[asyncio.Future] = None  # type: ignore[type-arg]
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

        # list_models cache: the provider catalog changes slowly, so a live fetch
        # is cached for a few minutes. (monotonic_ts, models) or None until first
        # fetch. Cleared implicitly by TTL expiry in _handle_list_models.
        self._models_cache: Optional[tuple[float, list[dict]]] = None
        self._models_cache_ttl = 300.0  # seconds

        # SECURITY: MCP auth tokens (Phase 2) are registered here so they are
        # scrubbed from EVERYTHING the daemon emits — stdout (JSON-RPC responses +
        # daemon-message events) via _write, and stderr (logs) via the filter
        # below. The token itself lives only in the OS keychain (UI side) and,
        # transiently, in os.environ + the spawned server's subprocess env. It is
        # never written to disk, the config, or any tracked file.
        self._secret_values: set[str] = set()
        # Attach the redactor to the root logger AND its handlers. Handler-level
        # filters are essential: a filter on the root LOGGER is NOT applied to
        # records propagated up from child loggers (deepagents, mcp, …), but a
        # filter on the root HANDLERS is. __main__ installs a stderr StreamHandler
        # via basicConfig before the daemon is built, so it exists here.
        _redactor = _SecretRedactingFilter(self._secret_values)
        _root = logging.getLogger()
        _root.addFilter(_redactor)
        for _h in _root.handlers:
            _h.addFilter(_redactor)

        self._real_stdout = sys.stdout
        sys.stdout = sys.stderr

    # ── Wire I/O ──────────────────────────────────────────────────────────────

    def _register_secret(self, value: str) -> None:
        """Register a secret so it is redacted from all daemon output."""
        if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN:
            self._secret_values.add(value)

    def _write(self, obj: dict) -> None:
        # Defense-in-depth: redact any registered secret from the serialized line
        # before it reaches the real stdout (which the UI reads verbatim). Even an
        # accidental echo of a token in a response/event/error cannot leak here.
        line = json.dumps(obj, ensure_ascii=True)
        if self._secret_values:
            line = _redact_secrets(line, self._secret_values)
        self._real_stdout.write(line + "\n")
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
            elif method == "list_models":          await self._handle_list_models(req_id)
            elif method == "list_mcp_servers":     await self._handle_list_mcp_servers(req_id)
            elif method == "connect_mcp_server":   await self._handle_connect_mcp_server(req_id, params)
            elif method == "disconnect_mcp_server": await self._handle_disconnect_mcp_server(req_id, params)
            elif method == "abort":                await self._handle_abort(req_id)
            elif method == "resume_plan":          await self._handle_resume_plan(req_id, params)
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

    # ── Model discovery (live provider catalog → free + tool-capable subset) ───
    #
    # Fetches the provider's /models endpoint and filters to the models the agent
    # can actually drive (free, tool-capable, >=32k context). The result is the
    # source for the GUI model dropdown. SECURITY: the API key is sent ONLY in the
    # request header inside provider.models.fetch_models — the rows returned here
    # carry model ids/names + context length, never the key, so it cannot reach
    # this response, the daemon-message stream, or logs.

    async def _handle_list_models(self, req_id: Any) -> None:
        models = await self._get_models()
        self.send_response(req_id, {"models": models})

    async def _get_models(self) -> list[dict]:
        """Return the usable-model list, cached for a few minutes.

        On any fetch failure (offline, non-OpenRouter provider, bad endpoint),
        falls back to the .env-declared models so the dropdown is never empty.
        Never raises — model discovery must not take the daemon down.
        """
        import time

        from hcode_v2.provider.models import (
            fallback_models, fetch_models, filter_usable_models,
        )
        from hcode_v2.utils.config import Config

        now = time.monotonic()
        if self._models_cache is not None:
            ts, cached = self._models_cache
            if now - ts < self._models_cache_ttl:
                return cached

        config = Config.from_env()
        # No base_url means we can't introspect a catalog (e.g. bare OpenAI default
        # or Anthropic) — go straight to the configured fallback.
        if config.base_url:
            try:
                catalog = await fetch_models(config.base_url, config.api_key)
                models = filter_usable_models(catalog)
                if models:
                    self._models_cache = (now, models)
                    return models
                logger.info("list_models: provider returned no usable models; using fallback")
            except Exception as exc:  # noqa: BLE001 - any failure → graceful fallback
                logger.warning("list_models fetch failed (%s); using fallback", exc)

        models = fallback_models()
        # Cache the fallback too, but briefly, so a transient outage doesn't hammer
        # the provider on every dropdown mount while still recovering reasonably soon.
        self._models_cache = (now, models)
        return models

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
        """Return the MCP client factory (test-injected, else stderr-capturing)."""
        if self._mcp_client_factory is not None:
            return self._mcp_client_factory
        # HCode-side factory (NOT vendored): injects env_required secrets, strips
        # None tool args, AND captures the server's stderr so a failed connect
        # reports the REAL reason (e.g. an npm/PyPI 404) instead of the SDK's
        # opaque "Connection closed".
        from hcode_v2.agent.mcp_env import capturing_client_factory
        return capturing_client_factory

    def _catalog(self) -> dict[str, dict]:
        """Vendored KNOWN_SERVERS with HCode command corrections overlaid.

        The vendored catalog ships fetch/sqlite commands pointing at npm packages
        that don't exist (npx → 404). merged_catalog() corrects them to the real
        uvx-based servers without editing libs/deepagents.
        """
        from hcode_v2.agent.mcp_catalog import merged_catalog
        return merged_catalog()

    def _server_def(self, name: str) -> dict | None:
        """Resolve a server definition from the corrected catalog or config."""
        catalog = self._catalog()
        if name in catalog:
            return catalog[name]
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
        "needs-auth" (Phase 2). An empty result means we can connect.

        Alias-aware: a required token var is satisfied if it OR any of the
        server's accepted alias names is present (so a keychain token under the
        canonical GITHUB_PERSONAL_ACCESS_TOKEN and a legacy GITHUB_TOKEN in .env
        both pass the gate).
        """
        import os
        from hcode_v2.agent.mcp_catalog import token_aliases
        required = (self._catalog().get(name) or server_def or {}).get("env_required", [])
        env_block = (server_def or {}).get("env", {})
        aliases = set(token_aliases(name))

        def _present(var: str) -> bool:
            # The var itself, or — if it's part of the server's token-alias set —
            # any alias, satisfies the requirement.
            candidates = aliases if var in aliases else {var}
            return any(c in os.environ or c in env_block for c in candidates)

        return [v for v in required if not _present(v)]

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
        catalog = self._catalog()
        configured = self._configured_servers()
        # Catalog ∪ configured (config wins for command/args/env/description).
        names = list(dict.fromkeys([*catalog.keys(), *configured.keys()]))
        servers: list[dict] = []
        for name in names:
            defn = {**(catalog.get(name) or {}), **configured.get(name, {})}
            row = {
                "name": name,
                "description": defn.get("description", ""),
                "configured": name in configured,
                **self._server_status(name, defn, name in configured),
            }
            servers.append(row)
        self.send_response(req_id, {"servers": servers})

    def _apply_mcp_secrets(self, params: dict) -> None:
        """Phase 2: apply auth tokens supplied securely with a connect request.

        The native app's Tauri ``connect_mcp_server`` command reads the token from
        the OS keychain (account ``mcp:<server>``) and passes it here as
        ``params["secrets"] = {ENV_VAR: token}`` — over the daemon's stdin, which
        is NEVER echoed to stdout/events. Each value is (1) registered with the
        redaction filter so it can never appear in any daemon output, and (2)
        placed into ``os.environ`` (in-memory only). The existing
        ``resolve_server_env`` then injects it into the spawned server's env, and
        the same os.environ is read by the agent's factory on rebuild — so the
        token reaches both without ever touching disk, the config, or git.

        On the dev/CLI path there is no keychain and no ``secrets`` param; the
        token comes from ``.env`` → ``os.environ`` exactly as before.
        """
        secrets = params.get("secrets")
        if not isinstance(secrets, dict):
            return
        for var, value in secrets.items():
            if isinstance(var, str) and isinstance(value, str) and value:
                self._register_secret(value)   # redact from all output FIRST
                os.environ[var] = value         # in-memory; never written to disk

    async def _handle_connect_mcp_server(self, req_id: Any, params: dict) -> None:
        name: str = params.get("server", "")
        server_def = self._server_def(name)
        if server_def is None:
            self.send_response(req_id, error={"code": -32602, "message": f"Unknown MCP server: {name!r}"})
            return

        # Phase 2: apply any securely-supplied auth token BEFORE the needs-auth
        # check, so a token-based server (github/gitlab) now passes the gate and
        # connects. Registered for redaction the instant it arrives.
        self._apply_mcp_secrets(params)

        # A server still missing a required token after secrets are applied is
        # surfaced as needs-auth and NOT spawned (no token to authenticate with).
        missing = self._missing_required_env(name, server_def)
        if missing:
            self.send_response(req_id, {
                "status": "needs-auth", "server": name,
                "message": f"Requires {', '.join(missing)}. Add the token in the server's secure field.",
            })
            return

        # Persist to config FIRST so the agent picks the server up on its next
        # build (cache eviction keys on the config signature). Persist the
        # CORRECTED def (from the catalog) so the agent spawns the working command
        # too. Only KNOWN presets are written; configured customs are left as-is.
        from deepagents.mcp.client import MCPClientManager
        if name in self._catalog():
            MCPClientManager(config_path=self._mcp_config).add_server_to_config(name, server_def)

        # Actually connect: real subprocess + init handshake + list_tools. Catch
        # BaseException (not just Exception): when a server dies mid-handshake the
        # anyio stdio teardown can raise an ExceptionGroup/BaseException, and one
        # bad server must never escape to crash the daemon's read loop. The
        # capturing client folds the server's stderr into a diagnostic message.
        # KeyboardInterrupt/SystemExit are re-raised inside the client.
        try:
            client = await self._connect_live(name, server_def)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 - isolate per-server failures
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

    async def _handle_abort(self, req_id: Any) -> None:
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()
            self.send_response(req_id, {"status": "aborting"})
        else:
            self.send_response(req_id, {"status": "no_task_running"})

    async def _handle_resume_plan(self, req_id: Any, params: dict) -> None:
        """Resolve a paused plan review with the user's accept/reject decision.

        The running task is awaiting ``_plan_decision_future``; setting its result
        unblocks _run_task, which then resumes the graph (accept → execute; reject
        → clean stop). If nothing is paused, this is a harmless no-op.
        """
        accept = bool(params.get("accept"))
        fut = self._plan_decision_future
        if fut is None or fut.done():
            self.send_response(req_id, {"status": "no_pending_plan"})
            return
        fut.set_result(accept)
        self.send_response(req_id, {"status": "resumed", "accept": accept})

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
        # model: the model id to run this task against, or None = .env default.
        # Validate: must be a non-empty string; anything else → None (default).
        raw_model = params.get("model")
        model: Optional[str] = raw_model if isinstance(raw_model, str) and raw_model.strip() else None
        # plan_review: pause at the plan→execute boundary for accept/reject.
        # Default False = unchanged run-through.
        plan_review = bool(params.get("plan_review"))
        self._current_task = asyncio.create_task(
            self._run_task(req_id, task, thread_id, work_dir, active_skills, model, plan_review)
        )

    async def _handle_run_workflow_dispatch(self, req_id: Any, params: dict) -> None:
        name: str = params.get("workflow", "")
        thread_id: str = params.get("thread_id") or f"gui_wf_{abs(hash(name))}"
        self._current_task = asyncio.create_task(self._run_task(req_id, f"run workflow {name}", thread_id))

    # ── Model fallback (429 resilience) ────────────────────────────────────────
    #
    # When enabled, a rate-limited primary model retries with backoff then falls
    # back to the next free tool-capable model. OFF by default → no resilience
    # wrapper is built → zero regression, zero added latency.
    #
    # Enable + order resolution (first wins):
    #   1. HCODE_FALLBACK_MODELS="a,b,c"  — explicit ordered fallbacks (enables it)
    #   2. HCODE_MODEL_FALLBACK=auto       — derive from list_models (free + tool-
    #                                        capable), minus the primary, capped
    #   else                               — OFF (empty list)

    _MAX_AUTO_FALLBACKS = 3

    async def _resolve_fallbacks(self, primary: Optional[str]) -> list[str]:
        """Return the ordered fallback model names (excluding the primary), or []."""
        explicit = os.getenv("HCODE_FALLBACK_MODELS", "").strip()
        if explicit:
            names = [s.strip() for s in explicit.split(",") if s.strip()]
        elif os.getenv("HCODE_MODEL_FALLBACK", "").strip().lower() == "auto":
            try:
                models = await self._get_models()  # cached free + tool-capable list
            except Exception:  # noqa: BLE001 - discovery must never break a run
                models = []
            names = [m["id"] for m in models]
        else:
            return []  # feature off

        # Drop the primary (it's already index 0 in the resilient client) + dups.
        primary_name = primary or ""
        out: list[str] = []
        for n in names:
            if n and n != primary_name and n not in out:
                out.append(n)
        return out[: self._MAX_AUTO_FALLBACKS]

    # ── run_task — C2: astream_events + StreamingBridge ──────────────────────

    async def _run_task(self, req_id: Any, task: str, thread_id: str, work_dir: Optional[str] = None, active_skills: Optional[list[str]] = None, model: Optional[str] = None, plan_review: bool = False) -> None:
        """Execute one task, streaming events to the client via StreamingBridge."""
        self.send_response(req_id, {"status": "started", "thread_id": thread_id})
        try:
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
                # Evict the cached agent when work_dir, active_skills, the MCP
                # config, OR the model changes — all are baked into the agent at
                # build time (backend root_dir, the skills middleware allowlist,
                # the set of MCP servers whose tools the factory wires in, and the
                # chat model, respectively). The MCP key makes a connect/disconnect
                # actually reach the running agent on the next task; the model key
                # makes a model switch rebuild on the next task instead of running
                # on the stale one.
                skills_key = frozenset(active_skills) if active_skills else None
                mcp_key = self._mcp_config_signature()
                cached = self._agents.get(thread_id)
                if cached is not None and (
                    cached["work_dir"] != work_dir
                    or cached["skills_key"] != skills_key
                    or cached["mcp_key"] != mcp_key
                    or cached["model"] != model
                    or cached["plan_review"] != plan_review
                ):
                    cached = None
                    del self._agents[thread_id]
                if cached is None:
                    # 429 resilience: resolve the fallback order (empty = feature
                    # off → unchanged single-model agent). on_fallback surfaces a
                    # switch to the UI as a model_fallback event.
                    fallback_models = await self._resolve_fallbacks(model)
                    on_fallback = (
                        (lambda frm, to: self.emit_event(
                            "model_fallback",
                            {"from": frm, "to": to,
                             "message": f"{frm} rate-limited — switched to {to}"},
                        ))
                        if fallback_models else None
                    )
                    agent = await create_hcode_agent(
                        skills_dir=self._skills_dir,
                        workflows_dir=self._workflows_dir,
                        mcp_config=self._mcp_config,
                        session_id=thread_id,
                        persist=True,
                        work_dir=work_dir,
                        active_skills=list(skills_key) if skills_key else None,
                        model=model,
                        fallback_models=fallback_models,
                        on_fallback=on_fallback,
                        plan_review=plan_review,
                    )
                    self._agents[thread_id] = {
                        "agent": agent, "work_dir": work_dir,
                        "skills_key": skills_key, "mcp_key": mcp_key,
                        "model": model, "plan_review": plan_review,
                    }
                else:
                    agent = cached["agent"]

                # recursion_limit MUST be explicit on the astream_events path:
                # langchain_core stamps its default (25) into the config, which
                # overrides the agent's bound 9999 and kills tasks after ~5 tool
                # rounds. Mirrors the CLI fix (cli/main.py:242). Matters more now
                # that PEV's execute phase runs up to 15 rounds. Built once and
                # reused for the resume streams so they target the SAME thread.
                config = {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 1000,
                }
                last_text = await self._stream_agent(
                    agent, {"messages": [HumanMessage(content=task)]}, config, bridge
                )

                # Plan review (HITL): when enabled, the graph pauses at the
                # plan→execute boundary via PlanReviewMiddleware.interrupt(). The
                # initial stream above then ends with the interrupt pending; here
                # we surface the plan, await the user's decision, and resume — a
                # port of the CLI's aget_state → Command(resume=...) loop
                # (cli/main.py). When plan_review is OFF this block is skipped
                # entirely, so a normal run is byte-identical to before.
                if plan_review:
                    from langgraph.types import Command
                    while True:
                        pending = await self._find_plan_interrupt(agent, config)
                        if pending is None:
                            break
                        self.emit_event("plan_review", {"plan": pending.get("plan", "")})
                        accept = await self._await_plan_decision()  # cancellable by Abort
                        if not accept:
                            self.emit_event(
                                "plan_rejected",
                                {"message": "Plan rejected — execution skipped."},
                            )
                        last_text = await self._stream_agent(
                            agent, Command(resume={"accept": accept}), config, bridge
                        )

                bridge.finalize(summary=last_text)

            except Exception as exc:
                logger.error("run_task streaming failed: %s", exc)
                self.emit_event("error", {"message": str(exc), "recoverable": False})

        except asyncio.CancelledError:
            self.emit_event("aborted", {"message": "Task aborted — last completed step preserved."})
            raise
        finally:
            self._current_task = None
            self._plan_decision_future = None

    async def _stream_agent(self, agent: Any, input_: Any, config: dict, bridge: Any) -> str:
        """Drive one astream_events pass, forwarding events to the bridge.

        Returns the last AI message text (the done summary). Used for both the
        initial run and each plan-review resume so they share identical event
        handling and config (same thread_id).
        """
        last_text = ""
        async for event in agent.astream_events(input_, config=config, version="v2"):
            bridge.process_event(event)
            if event.get("event") == "on_chat_model_end":
                msg = event.get("data", {}).get("output", {})
                if hasattr(msg, "content"):
                    last_text = msg.content if isinstance(msg.content, str) else ""
        return last_text

    async def _find_plan_interrupt(self, agent: Any, config: dict) -> Optional[dict]:
        """Return the pending plan-review interrupt value, or None if not paused."""
        from hcode_v2.agent.plan_review import PLAN_REVIEW_INTERRUPT
        state = await agent.aget_state(config)
        for intr in getattr(state, "interrupts", None) or []:
            value = getattr(intr, "value", None)
            if isinstance(value, dict) and value.get("type") == PLAN_REVIEW_INTERRUPT:
                return value
        return None

    async def _await_plan_decision(self) -> bool:
        """Block until resume_plan resolves the decision (True=accept/False=reject).

        The awaiting future keeps _run_task's task alive while paused, so Abort's
        task.cancel() propagates a CancelledError here and stops the paused run.
        """
        loop = asyncio.get_event_loop()
        self._plan_decision_future = loop.create_future()
        try:
            return await self._plan_decision_future
        finally:
            self._plan_decision_future = None

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
