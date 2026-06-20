# Per-Action Human Approval — Design Map

Design notes for v2's per-action human approval (ask before edit/write and before
running bash), modeled on Claude Code / Cursor / OpenCode. Investigation across
OpenCode (`_refs/opencode`), v1 (`hcode`), and v2. Not yet implemented.

## The two seams in v2

Every tool runs inside the LangGraph/deepagents graph, driven by
`agent.astream_events(...)` inside a `with renderer:` Rich Live block
(`cli/main.py`). Two places can gate a tool:

### Seam A — `wrap_tool_call` / `awrap_tool_call` middleware (HCode-side)

The execution seam our `_PathContainmentMiddleware` and `SafetyGuardMiddleware`
already use. Proven to short-circuit: path containment returns a `ToolMessage`
*instead of* calling `handler`, so the tool never runs. `awrap_tool_call` is
`async def`, so it can `await` user input between request and `handler`, returning
either `await handler(request)` (approved) or `ToolMessage("user denied", ...)`
(denied, fed back to the model). Sees the *final* tool call (after containment
re-homes paths). Most surgical, but everything (prompt, session-memory, rejection
messages) is hand-written.

### Seam B — `interrupt_on` + `HumanInTheLoopMiddleware` (the blessed way)

`create_deep_agent` already accepts `interrupt_on` (`graph.py`) and appends
`HumanInTheLoopMiddleware` when set — but v2 never passes it (`factory.py`). It's
wired and dormant. The middleware interrupts in `after_model`: it batches every
configured tool call into one `interrupt(hitl_request)` and pauses the graph.
Requires a checkpointer — **v2 already has one** (`factory.py`: SQLite when
`persist=True`, else `MemorySaver`), so it's viable today.

- Config: `interrupt_on={"bash": {"allowed_decisions": ["approve","reject"]}}`,
  `{"edit": True}` (= all four), etc.
- Decisions: `approve | edit | reject | respond`
  (`langchain/agents/middleware/human_in_the_loop.py`):
  - approve → tool runs as-is
  - edit → rewrites tool-call args, then runs
  - reject → tool skipped, synthetic *error* ToolMessage (optional message) to model
  - respond → tool skipped, synthetic *success* ToolMessage (human answers for the tool)
- Surfaces in `astream_events`: the stream **ends normally** (no exception) with
  the graph parked. Detect *after* the `async for` via
  `state = await agent.aget_state(config)`; `state.interrupts` non-empty ⇒ paused.
  Payload: `state.interrupts[0].value` → `{action_requests, review_configs}`.
- Resume: call again with `Command(resume={"decisions":[...]})` and the same
  `thread_id`. Parallel tool calls collapse into ONE interrupt with N
  `action_requests`; decisions must match **order and count** or it raises.

## Recommendation: Seam B, TTY-gated

Use `interrupt_on` + `HumanInTheLoopMiddleware` for the interactive chat loop,
gated behind a TTY/flag check. Why:

1. **Already built and tested** — flows through `create_deep_agent`, ships in
   langchain, checkpointer present, `test_hitl.py` proves pause/resume. Seam A
   means re-deriving prompt/park/session-memory/rejection-message logic.
2. **Pause happens between stream invocations, not inside the Live block** — the
   `with renderer` block has already exited when we read `state.interrupts`, so we
   sidestep the worst Rich-Live-vs-prompt conflict.
3. **Matches OpenCode's architecture for the daemon** (see below) — same
   pattern serves CLI + desktop from one mechanism.
4. Decision richness (approve/edit/reject+message/respond) already matches
   Claude Code / Cursor.

**Caveat:** `interrupt_on` fires in `after_model` on the *raw* tool call, *before*
`_PathContainmentMiddleware` re-homes the path — the prompt may show a different
path than what executes. If that matters, normalize in an `InterruptOnConfig`
`description` callable, or move containment ahead of approval.

**Allow-session gap:** the HITL middleware only knows per-call `approve`. "Allow for
this session" (OpenCode's persistent `approved[]` rules) is NOT built in. Add a
small HCode-side session allowlist (tool name / command prefix) that *removes* a
tool from the interrupt set once the user picks "always" — a Seam-A shim feeding
Seam B.

## TTY / non-interactive gate (must not hang)

No TTY detection exists in v2 today. Three non-interactive call sites:
- `run`/`analyze`/`explore` → `_run_agent_task` → `ainvoke` (no streaming, no
  interrupt handling). With `interrupt_on` set, `ainvoke` returns *parked* and
  silently does nothing — so this path must NOT set `interrupt_on` (or auto-approve).
- The **daemon** (`daemon/server.py`) streams to the Tauri client — no terminal.
- Piped / CI stdin.

Gate: `enable_approval = sys.stdin.isatty() and sys.stdout.isatty()` (or explicit
`--autonomous` / `--yes`, mirroring v1's `auto_confirm_commands`), checked once at
agent construction. When false: don't pass `interrupt_on` (or resume immediately
with all-`approve`). Async input via `prompt_toolkit`'s `prompt_async` (already used
in the chat loop) — a raw `input()` inside the asyncio loop blocks the event loop.

## Riskiest part

1. **The Rich Live ↔ interactive-prompt handoff.** `LiveTurnRenderer`
   (`cli/live.py`) owns the terminal between `Live.start()` / `Live.stop()`. Must
   tear down the Live region (restore cursor/terminal) before `prompt_async`, then
   rebuild renderer state on resume without losing scrollback. There is no
   `suspend()` today.
2. **Order/count-sensitive resume.** Parallel tool calls → one interrupt with N
   `action_requests`; must reply with exactly N decisions in order or it raises
   (`human_in_the_loop.py`). Prototype single-call first, then generalize.
3. The daemon path has no interrupt handling at all yet — wiring the Tauri client
   to render the prompt and POST a resume is net-new protocol work.

## OpenCode server/client pattern (for the daemon)

OpenCode's approval (`packages/opencode/src/permission/index.ts`) is a server/client
split mediated by an event bus + a parked promise:
- A tool calls `ctx.ask(...)`; `Permission.ask` evaluates rulesets, and if any
  pattern is `"ask"` it makes an Effect `Deferred`, stores it in a `pending` Map,
  **publishes `permission.asked`**, and **awaits the Deferred** — suspending the tool.
- The client (TUI/desktop) renders the prompt and **POSTs to
  `permission/:requestID/reply`** → `Permission.reply` resolves/fails the Deferred →
  the tool resumes or throws. Choices: `once | always | reject(+message)`. "always"
  pushes a runtime allow-rule (session memory). Bash asks per command-pattern
  (`patterns`/`always`), enabling "always allow `git status`".

The agent blocks on a promise, not a terminal — anything (TUI, web, API) can resolve
it. v2's daemon already streams events to the Tauri client, so the interrupt payload
(`action_requests`/`review_configs`) ≈ `permission.asked`, and `Command(resume=...)`
≈ `reply`. Same proven pattern, one mechanism for CLI + desktop.

## Shipping plan — slice (a) first

Smallest correct slice before generalizing:
- **bash-only** (`interrupt_on={"bash": ...}`)
- **approve / reject** only (no edit/respond yet)
- **TTY-gated** (skip entirely when non-interactive)
- **single tool call** (defer the parallel N-decisions case)

Then layer on: edit/write tools, allow-session memory, parallel-call ordering,
edit/respond decisions, and the daemon/Tauri client path.
