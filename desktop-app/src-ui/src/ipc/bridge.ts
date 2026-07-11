/**
 * IPC Bridge — v2 edition with three transport paths:
 *
 *   VITE_MOCK=true        Mock fixtures (default, CI-safe, no daemon needed).
 *   VITE_WS_URL=ws://…    WebSocket proxy (dev-launcher mode, no Tauri needed).
 *   Otherwise             Tauri invoke/listen (production desktop app, C4 built).
 *
 * The WS path is identical to what Tauri's daemon.rs provides in Rust:
 *   - wsInvoke sends a JSON-RPC request and awaits the matching response.
 *   - wsListen subscribes to unsolicited daemon-message notifications.
 *
 * v2 additions vs v1:
 *   listSkills, listWorkflows, runWorkflow
 *   listMcpServers, connectMcpServer, disconnectMcpServer
 */
import type { HcodeMessage, FileEntry, DaemonInfo, DaemonError } from '../types';

// ── Transport selection ───────────────────────────────────────────────────────
//
// Runtime config takes precedence over build-time Vite env so one static image
// works in any environment. A Docker entrypoint writes /config.js (loaded before
// the app bundle) which sets window.HCODE_WS_URL / window.HCODE_MOCK from env.
// Resolution: window.HCODE_WS_URL → VITE_WS_URL → mock fallback.

type RuntimeConfig = { HCODE_WS_URL?: string; HCODE_MOCK?: string };
const _runtime: RuntimeConfig =
    typeof window !== 'undefined' ? (window as unknown as RuntimeConfig) : {};

const MOCK_MODE: boolean =
    (import.meta.env as Record<string, string>).VITE_MOCK === 'true' ||
    _runtime.HCODE_MOCK === 'true';

const WS_URL: string | undefined =
    (_runtime.HCODE_WS_URL && _runtime.HCODE_WS_URL.trim()) ||
    (import.meta.env as Record<string, string>).VITE_WS_URL ||
    undefined;

const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
type ListenFn = (event: string, handler: (e: { payload: unknown }) => void) => Promise<() => void>;

async function getInvoke(): Promise<InvokeFn> {
    if (MOCK_MODE) return mockInvoke;
    if (WS_URL)    return wsInvoke;
    if (isTauri) {
        const { invoke } = await import('@tauri-apps/api/core');
        return invoke as InvokeFn;
    }
    console.warn('[IPC] No WS URL and not Tauri — falling back to mock daemon.');
    return mockInvoke;
}

async function getListen(): Promise<ListenFn> {
    if (MOCK_MODE) return mockListen;
    if (WS_URL)    return wsListen;
    if (isTauri) {
        const { listen } = await import('@tauri-apps/api/event');
        return listen as unknown as ListenFn;
    }
    return mockListen;
}

if (MOCK_MODE)   console.info('[IPC] mock daemon (VITE_MOCK or window.HCODE_MOCK)');
else if (WS_URL) console.info(`[IPC] WS transport — ${WS_URL}`);

// ── WebSocket transport ───────────────────────────────────────────────────────

let _ws: WebSocket | null = null;
let _wsConnecting: Promise<WebSocket> | null = null;
let _wsRequestId = 0;
const _wsPending = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
const _wsEventListeners = new Map<string, Array<(e: { payload: unknown }) => void>>();

function _getWs(): Promise<WebSocket> {
    if (_ws && _ws.readyState === WebSocket.OPEN) return Promise.resolve(_ws);
    if (_wsConnecting) return _wsConnecting;
    _wsConnecting = new Promise((resolve, reject) => {
        const socket = new WebSocket(WS_URL!);
        socket.onopen = () => { _ws = socket; _wsConnecting = null; resolve(socket); };
        socket.onerror = () => { _wsConnecting = null; reject(new Error(`[WS] Cannot connect to ${WS_URL}`)); };
        socket.onclose = () => { _ws = null; };
        socket.onmessage = ({ data }) => {
            try {
                const msg = JSON.parse(data as string);
                // JSON-RPC response — has id + result/error
                if (msg.jsonrpc && msg.id !== undefined) {
                    const pending = _wsPending.get(String(msg.id));
                    if (pending) {
                        _wsPending.delete(String(msg.id));
                        if (msg.error) pending.reject(new Error(msg.error.message ?? 'RPC error'));
                        else pending.resolve(msg.result ?? null);
                    }
                    return;
                }
                // Event notification — broadcast to listeners
                const listeners = _wsEventListeners.get('daemon-message') ?? [];
                listeners.forEach(fn => fn({ payload: msg }));
            } catch (e) {
                console.error('[WS] parse error', data, e);
            }
        };
    });
    return _wsConnecting;
}

async function wsInvoke(cmd: string, args?: Record<string, unknown>, timeoutMs = 30_000): Promise<unknown> {
    const socket = await _getWs();
    const id = String(++_wsRequestId);
    return new Promise((resolve, reject) => {
        _wsPending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ jsonrpc: '2.0', id, method: cmd, params: args ?? {} }));
        setTimeout(() => {
            if (_wsPending.has(id)) {
                _wsPending.delete(id);
                reject(new Error(`[WS] Timeout: ${cmd}`));
            }
        }, timeoutMs);
    });
}

async function wsListen(
    event: string,
    handler: (e: { payload: unknown }) => void,
): Promise<() => void> {
    await _getWs();  // ensure connected
    if (!_wsEventListeners.has(event)) _wsEventListeners.set(event, []);
    _wsEventListeners.get(event)!.push(handler);
    return () => {
        const list = _wsEventListeners.get(event) ?? [];
        _wsEventListeners.set(event, list.filter(fn => fn !== handler));
    };
}

// ── Mock transport ────────────────────────────────────────────────────────────

const MOCK_SKILLS = [
    { name: 'clean-code',              description: 'Clean code principles',             category: 'Quality',  lastUsed: null },
    { name: 'code-review',             description: 'Structured code review',            category: 'Quality',  lastUsed: null },
    { name: 'concise-planning',        description: 'Efficient planning approach',       category: 'Planning', lastUsed: null },
    { name: 'error-handling-patterns', description: 'Robust error handling',             category: 'Quality',  lastUsed: null },
    { name: 'pytest-idioms',           description: 'Pytest best practices',             category: 'Testing',  lastUsed: null },
    { name: 'run-tests-before-done',   description: 'Always run tests before closing',  category: 'Testing',  lastUsed: null },
    { name: 'systematic-debugging',    description: 'Structured debugging methodology', category: 'Quality',  lastUsed: null },
    { name: 'tdd-lite',                description: 'Lightweight TDD approach',          category: 'Testing',  lastUsed: null },
];
const MOCK_WORKFLOWS = [
    { name: 'full-feature', description: 'Plan → implement → test → document', stepCount: 4, lastRun: null, status: 'idle' },
    { name: 'quick-fix',    description: 'Patch, verify, commit',              stepCount: 3, lastRun: null, status: 'idle' },
];
// Phase 1/2 shape: status + toolCount + configured + (needs-auth for token servers
// until a token is saved). github is needs-auth; no-auth servers are connectable.
const MOCK_MCP_SERVERS = [
    { id: 'filesystem', name: 'filesystem', description: 'Local file access',  status: 'disconnected', toolCount: 0, configured: false },
    { id: 'web-fetch',  name: 'web-fetch',  description: 'Fetch URLs, scrape', status: 'disconnected', toolCount: 0, configured: false },
    { id: 'github',     name: 'github',     description: 'GitHub integration', status: 'needs-auth',   toolCount: 0, configured: false },
];

// Demo-only: tracks which keychain accounts (e.g. "mcp:github") have had a token
// saved this session — the BOOLEAN only, never the token value. Lets the keyless
// demo show a token-gated server connecting after a token is entered + saved.
const _mockSavedKeys = new Set<string>();

// Mock session list. "default" is included so the UI's filter (which hides the
// CLI's single-shot default.db) is demoable keyless, like the real daemon returns.
const MOCK_SESSIONS: string[] = ['default', 'gui_demo_1', 'gui_demo_2'];
// Mock model list — realistic free + tool-capable OpenRouter ids so the dropdown
// is populated keyless. Mirrors the {id, name, context_length} daemon shape.
const MOCK_MODELS = [
    { id: 'gpt-oss (default)',           name: 'gpt-oss (default)',                    context_length: 0 },
    { id: 'qwen/qwen-2.5-coder-32b:free', name: 'Qwen 2.5 Coder 32B (free)',           context_length: 131072 },
    { id: 'deepseek/deepseek-chat:free',  name: 'DeepSeek V3 (free)',                  context_length: 65536 },
    { id: 'google/gemini-2.0-flash-exp:free', name: 'Gemini 2.0 Flash Experimental (free)', context_length: 1048576 },
];

// Mock workspace shown when Open Folder is used in pure-mock (VITE_MOCK) mode,
// so the file tree never hangs on "Loading files..." (mock used to return []).
const MOCK_ROOT_TREE: FileEntry[] = [
    { name: 'src',            path: '/mock-project/src',            isDirectory: true },
    { name: 'README.md',      path: '/mock-project/README.md',      isDirectory: false },
    { name: 'pyproject.toml', path: '/mock-project/pyproject.toml', isDirectory: false },
];
const MOCK_SRC_TREE: FileEntry[] = [
    { name: 'hello.py', path: '/mock-project/src/hello.py', isDirectory: false },
    { name: 'utils.py', path: '/mock-project/src/utils.py', isDirectory: false },
];
const MOCK_FILES: Record<string, string> = {
    '/mock-project/README.md': '# Mock Project\n\nA demo workspace shown in mock mode (no real files on disk).\n',
    '/mock-project/pyproject.toml': '[project]\nname = "mock-project"\nversion = "0.1.0"\n',
    '/mock-project/src/hello.py': 'def greet(name: str) -> str:\n    return f"Hello, {name}"\n',
    '/mock-project/src/utils.py': 'def shout(text: str) -> str:\n    return text.upper() + "!"\n',
};

let _mockListeners: Array<(e: { payload: unknown }) => void> = [];
let _mockCancel: (() => void) | null = null;
// Plan review (HITL) demo: when a mock run pauses for review, this holds the
// continuation. resume_plan(accept) invokes it (accept → execute; reject → stop).
let _mockResume: ((accept: boolean) => void) | null = null;

/**
 * Emit a SINGLE coherent mock event stream for one task -- the SAME event
 * vocabulary bridge.py emits in live mode, telling the FULL demo story:
 * streaming -> plan -> two file patches -> LSP self-correction
 * (started -> 1 type error -> fix -> clean) -> verification passed -> done.
 * Mirrors the daemon's _mock_streaming_task so the VITE_MOCK path and the
 * daemon mock (dev.py --mock, Docker) tell the same story.
 *
 * Mock-only testing affordances:
 *   - task contains "fail" / "error" -> emit one `error` (single error surface,
 *     composer re-enables -- no resubmit graveyard).
 *   - mode "fast" -> skip the plan phase.
 */
function _fireMock(task: string, mode: 'planning' | 'fast' = 'planning', planReview = false): void {
    _mockCancel?.();
    _mockResume = null;
    const emit = (msg: HcodeMessage) => _mockListeners.forEach(fn => fn({ payload: msg }));
    const timers: ReturnType<typeof setTimeout>[] = [];
    let t = 0;
    const after = (ms: number, fn: () => void) => timers.push(setTimeout(fn, t += ms));
    _mockCancel = () => { timers.forEach(clearTimeout); _mockResume = null; };

    const wantsError = /\b(fail|error|boom)\b/i.test(task);
    // Keyless demo of the 429 resilience feature: a task mentioning "429" /
    // "rate limit" shows the primary→fallback switch as a model_fallback line.
    const wantsFallback = /\b(429|rate.?limit)\b/i.test(task);

    // Two files; hello.py is first written with a type error, then re-proposed
    // corrected after the LSP catches it (same path -> exercises diff dedupe).
    const helloBad = {
        path: 'src/hello.py',
        diff: '@@ -0,0 +1,3 @@\n+def greet(name: str) -> int:\n+    # returns a str but is annotated -> int\n+    return "Hello, " + name',
        backup: '',
        originalContent: '',
        newContent: 'def greet(name: str) -> int:\n    # returns a str but is annotated -> int\n    return "Hello, " + name\n',
    };
    const utils = {
        path: 'src/utils.py',
        diff: '@@ -0,0 +1,2 @@\n+def shout(text: str) -> str:\n+    return text.upper() + "!"',
        backup: '',
        originalContent: '',
        newContent: 'def shout(text: str) -> str:\n    return text.upper() + "!"\n',
    };
    const helloFixed = {
        path: 'src/hello.py',
        diff: '@@ -1,3 +1,2 @@\n-def greet(name: str) -> int:\n-    # returns a str but is annotated -> int\n-    return "Hello, " + name\n+def greet(name: str) -> str:\n+    return f"Hello, {name}"',
        backup: '',
        originalContent: 'def greet(name: str) -> int:\n    # returns a str but is annotated -> int\n    return "Hello, " + name\n',
        newContent: 'def greet(name: str) -> str:\n    return f"Hello, {name}"\n',
    };

    // ── Plan phase (skipped in fast mode) ──────────────────────────────────
    if (mode === 'planning') {
        after(0,   () => emit({ type: 'planning_started', payload: { timestamp: Date.now() } }));
        after(250, () => emit({ type: 'streaming_chunk',  payload: { content: `Analyzing "${task}"…`, phase: 'plan' } }));
        after(250, () => emit({ type: 'streaming_chunk',  payload: { content: '\nDrafting steps…', phase: 'plan' } }));
        if (wantsError) {
            after(350, () => emit({ type: 'error', payload: { message: 'Connection error.', suggestion: '' } }));
            return;
        }
        const planMd = `## Plan\n1. Inspect the codebase\n2. ${task}\n3. Run tests & verify`;
        after(350, () => emit({ type: 'plan_created', payload: {
            markdown: planMd,
            taskMd: task, implementationPlanMd: `Implement: ${task}`, timestamp: Date.now(),
        } }));
        // Plan review (HITL) demo: pause after the plan; resume_plan(accept) drives
        // the rest. Mirrors the daemon's plan_review interrupt + resume_plan loop.
        if (planReview) {
            after(150, () => emit({ type: 'plan_review', payload: { plan: planMd } }));
            _mockResume = (accept: boolean) => {
                _mockResume = null;
                if (!accept) {
                    emit({ type: 'plan_rejected', payload: { message: 'Plan rejected — execution skipped.' } });
                    emit({ type: 'done', payload: { summary: 'Plan rejected — nothing executed.', timestamp: Date.now() } });
                    return;
                }
                t = 0;  // schedule the resumed events relative to now, not plan time
                runExecuteVerify();
            };
            return;  // stop here until the user decides
        }
    } else if (wantsError) {
        after(0,   () => emit({ type: 'execution_started', payload: { timestamp: Date.now() } }));
        after(250, () => emit({ type: 'streaming_chunk',   payload: { content: `Working on: ${task}…`, phase: 'execute' } }));
        after(300, () => emit({ type: 'error', payload: { message: 'Connection error.', suggestion: '' } }));
        return;
    }

    // ── Execute + verify (also the plan-review "accept" continuation) ────────
    function runExecuteVerify(): void {
        after(250, () => emit({ type: 'execution_started', payload: { timestamp: Date.now() } }));
        // Keyless demo: surface a primary→fallback switch (the real daemon emits
        // this when ResilientChatModel exhausts retries on a rate-limited primary).
        if (wantsFallback) {
            after(150, () => emit({ type: 'model_fallback', payload: {
                from: 'qwen/qwen-2.5-coder-32b:free',
                to: 'deepseek/deepseek-chat:free',
                message: 'qwen/qwen-2.5-coder-32b:free rate-limited — switched to deepseek/deepseek-chat:free',
            } }));
        }
        after(200, () => emit({ type: 'streaming_chunk',   payload: { content: 'Writing the two files...', phase: 'execute' } }));
        after(200, () => emit({ type: 'task_update', payload: { markdown: '**Running tool:** `write` -> `src/hello.py`', step: 'tool:write' } }));
        after(250, () => emit({ type: 'task_update', payload: { markdown: '**Tool done:** `write`', step: 'tool_result:write' } }));
        after(150, () => emit({ type: 'file_patch', payload: helloBad }));
        after(200, () => emit({ type: 'task_update', payload: { markdown: '**Running tool:** `write` -> `src/utils.py`', step: 'tool:write' } }));
        after(250, () => emit({ type: 'task_update', payload: { markdown: '**Tool done:** `write`', step: 'tool_result:write' } }));
        after(150, () => emit({ type: 'file_patch', payload: utils }));

        // ── Verify with LSP: error -> fix -> clean (always shown, the W3.3 lane) ─
        after(300, () => emit({ type: 'verification_started', payload: { timestamp: Date.now() } }));
        after(200, () => emit({ type: 'task_update', payload: { markdown: '**Verifying with language server** (2 files)...', step: 'lsp_verify:started' } }));
        after(350, () => emit({ type: 'task_update', payload: { markdown: '**Language server found 1 error** - `src/hello.py:3` expected `int`, got `str`. Looping back to fix.', step: 'lsp_verify:errors' } }));
        after(250, () => emit({ type: 'task_update', payload: { markdown: '**Running tool:** `edit` -> `src/hello.py`', step: 'tool:edit' } }));
        after(250, () => emit({ type: 'task_update', payload: { markdown: '**Tool done:** `edit`', step: 'tool_result:edit' } }));
        after(150, () => emit({ type: 'file_patch', payload: helloFixed }));
        after(300, () => emit({ type: 'task_update', payload: { markdown: '**Language server check passed** - no errors.', step: 'lsp_verify:clean' } }));
        after(200, () => emit({ type: 'streaming_chunk', payload: { content: 'All checks pass.', phase: 'verify' } }));
        after(200, () => emit({ type: 'verification', payload: { markdown: `Task "${task}" complete - 2 files changed, type-checked clean.`, passed: true, testResults: '2 files - 0 type errors' } }));
        after(200, () => emit({ type: 'done', payload: { summary: `Completed: ${task}`, timestamp: Date.now() } }));
    }

    runExecuteVerify();
}

async function mockInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
    await new Promise(r => setTimeout(r, 80));
    switch (cmd) {
        case 'start_daemon': case 'daemon_health':
            return { status: 'running', uptime: 0, pid: 0, version: '2.0.0' } satisfies DaemonInfo;
        case 'stop_daemon':       return undefined;
        case 'open_folder_dialog': return '/mock-project';
        case 'list_directory': {
            const p = String(args?.path ?? '');
            return (p.endsWith('/src') || p.endsWith('\\src')) ? MOCK_SRC_TREE : MOCK_ROOT_TREE;
        }
        case 'read_file':         return MOCK_FILES[String(args?.path ?? '')] ?? '# (mock) empty file\n';
        case 'write_file':        return undefined;
        case 'save_api_key':
            // Demo only: remember WHICH providers were saved (never the value) so
            // the keyless demo can show a token-gated server connecting after save.
            if (typeof args?.provider === 'string') _mockSavedKeys.add(args.provider);
            return undefined;
        case 'get_api_key':       return null;
        case 'has_api_key':       return _mockSavedKeys.has(String(args?.provider ?? ''));
        case 'list_skills':       return { skills: MOCK_SKILLS };
        case 'list_workflows':    return { workflows: MOCK_WORKFLOWS };
        case 'list_sessions':     return { sessions: MOCK_SESSIONS };
        case 'list_models':       return { models: MOCK_MODELS };
        case 'list_mcp_servers':  return { servers: MOCK_MCP_SERVERS };
        case 'connect_mcp_server': {
            const server = String(args?.server ?? '');
            // Mirror the daemon: a token server stays needs-auth UNTIL a token has
            // been saved (to mcp:<server>), then connects with its tool count.
            if (server === 'github') {
                if (!_mockSavedKeys.has('mcp:github')) {
                    return { status: 'needs-auth', server, message: 'Requires GITHUB_TOKEN. Add a token in the secure field.' };
                }
                return { status: 'connected', server, toolCount: 26,
                         tools: ['create_issue', 'search_repositories', 'get_file_contents'].map(t => `mcp_github_${t}`) };
            }
            return { status: 'connected', server, toolCount: 4, tools: ['read', 'write', 'list', 'search'].map(t => `mcp_${server}_${t}`) };
        }
        case 'disconnect_mcp_server': return { status: 'disconnected', server: args?.server ?? '' };
        case 'run_workflow': { _fireMock(`run workflow ${args?.workflow ?? 'workflow'}`); return undefined; }
        case 'run_task': {
            const task = (args?.task as string) || 'task';
            // Simulate the daemon's single-flight guard for the keyless demo:
            // a task containing "busy" rejects exactly as a 2nd concurrent run_task would.
            if (/\bbusy\b/i.test(task)) throw new Error('A task is already running');
            console.info('[Mock IPC] run_task thread_id =', args?.thread_id ?? '(none)',
                         'model =', args?.model ?? '(default)',
                         'plan_review =', args?.plan_review ?? false);
            _fireMock(task, (args?.mode as 'planning' | 'fast') || 'planning', Boolean(args?.plan_review));
            return undefined;
        }
        case 'abort_task':        if (_mockCancel) { _mockCancel(); _mockCancel = null; } return undefined;
        case 'resume_plan':
            // HITL plan review: drive the paused mock run with the decision.
            if (_mockResume) { _mockResume(Boolean(args?.accept)); }
            return { status: _mockResume ? 'resumed' : 'no_pending_plan' };
        case 'approve_plan': case 'reject_plan':
        case 'accept_patch': case 'reject_patch': case 'rollback_all': return undefined;
        default: console.warn(`[Mock IPC] Unknown: ${cmd}`); return undefined;
    }
}

async function mockListen(
    event: string,
    handler: (e: { payload: unknown }) => void,
): Promise<() => void> {
    if (event === 'daemon-message') {
        _mockListeners.push(handler);
        return () => { _mockListeners = _mockListeners.filter(fn => fn !== handler); };
    }
    return () => {};
}

// ── Daemon lifecycle ──────────────────────────────────────────────────────────

export async function startDaemon(): Promise<DaemonInfo> {
    return (await getInvoke())('start_daemon') as Promise<DaemonInfo>;
}
export async function stopDaemon(): Promise<void> {
    return (await getInvoke())('stop_daemon') as Promise<void>;
}
export async function getDaemonHealth(): Promise<DaemonInfo> {
    return (await getInvoke())('daemon_health') as Promise<DaemonInfo>;
}

// ── Task commands ─────────────────────────────────────────────────────────────

export async function runTask(
    task: string, mode: 'planning' | 'fast', autonomous: boolean,
    threadId?: string, workDir?: string,
    activeSkills?: string[] | null,
    model?: string | null,
    planReview?: boolean,
): Promise<void> {
    const params: Record<string, unknown> = { task, mode, autonomous };
    if (threadId) params.thread_id = threadId;
    if (workDir)  params.work_dir  = workDir;
    // Only send active_skills when it's a non-null, non-empty subset.
    // Omitting it (or sending null) tells the daemon to load all skills.
    if (activeSkills && activeSkills.length > 0) params.active_skills = activeSkills;
    // Send the model NAME only (never a key) when one is chosen; omitting it
    // tells the daemon to use the .env default model (zero regression).
    if (model) params.model = model;
    // Only send plan_review when ON; omitting it = the default run-through.
    if (planReview) params.plan_review = true;
    return (await getInvoke())('run_task', params) as Promise<void>;
}
export async function abortTask(): Promise<void> {
    return (await getInvoke())('abort_task') as Promise<void>;
}
/** Plan review (HITL): resolve a paused plan with accept (continue) / reject (stop). */
export async function resumePlan(accept: boolean): Promise<void> {
    return (await getInvoke())('resume_plan', { accept }) as Promise<void>;
}
export async function approvePlan(): Promise<void> {
    return (await getInvoke())('approve_plan') as Promise<void>;
}
export async function rejectPlan(feedback: string): Promise<void> {
    return (await getInvoke())('reject_plan', { feedback }) as Promise<void>;
}
export async function acceptPatch(path: string): Promise<void> {
    return (await getInvoke())('accept_patch', { path }) as Promise<void>;
}
export async function rejectPatch(path: string): Promise<void> {
    return (await getInvoke())('reject_patch', { path }) as Promise<void>;
}
export async function rollbackAll(): Promise<void> {
    return (await getInvoke())('rollback_all') as Promise<void>;
}

// ── Tauri query correlator ────────────────────────────────────────────────────
//
// In native Tauri mode, the Rust rpc() helper sends a JSON-RPC request and
// returns Ok(()) immediately.  The daemon's actual response arrives later as a
// `daemon-message` Tauri event emitted by daemon.rs's spawn_output_reader.
// Streaming agent events (planning_started, done, …) also arrive on the same
// channel but carry a `type` field; JSON-RPC responses carry `jsonrpc + result/error`.
// tauriQuery() listens for the next JSON-RPC response, resolves with its result,
// and cleans up the listener — giving query RPCs the same await-able behaviour
// they already have on the WS path (wsInvoke).

async function tauriQuery(cmd: string, args?: Record<string, unknown>, timeoutMs = 10_000): Promise<unknown> {
    const { invoke } = await import('@tauri-apps/api/core');
    const { listen }  = await import('@tauri-apps/api/event');
    return new Promise((resolve, reject) => {
        let unlisten: (() => void) | undefined;
        const timer = setTimeout(() => {
            unlisten?.();
            reject(new Error(`[Tauri] Timeout awaiting response for: ${cmd}`));
        }, timeoutMs);
        const cleanup = () => { clearTimeout(timer); unlisten?.(); };
        listen<Record<string, unknown>>('daemon-message', event => {
            const msg = event.payload;
            // JSON-RPC responses have `jsonrpc` + `result` or `error`.
            // Streaming events have `type` only — never touch those.
            if ('jsonrpc' in msg && ('result' in msg || 'error' in msg)) {
                cleanup();
                if (msg.error) {
                    const e = msg.error as { message?: string };
                    reject(new Error(e.message ?? 'RPC error'));
                } else {
                    resolve(msg.result ?? null);
                }
            }
        }).then(fn => {
            unlisten = fn;
            // Register listener BEFORE invoking so no response is missed.
            invoke(cmd, args).catch(err => { cleanup(); reject(err as Error); });
        });
    });
}

/** Dispatch a query RPC through the right transport, with an optional timeout.
 *  - Native Tauri (not mock): tauriQuery — awaits the daemon-message response.
 *  - WS (not mock):           wsInvoke directly so the timeout is honored.
 *  - mock / fallback:         getInvoke() (timeout irrelevant).
 *  A long timeout is needed for connect_mcp_server: the first run of an
 *  npx/uvx-based MCP server downloads the package, which can take a minute. */
async function queryRpc(cmd: string, args?: Record<string, unknown>, timeoutMs?: number): Promise<unknown> {
    if (isTauri && !MOCK_MODE) return tauriQuery(cmd, args, timeoutMs ?? 10_000);
    if (WS_URL && !MOCK_MODE)  return wsInvoke(cmd, args, timeoutMs ?? 30_000);
    return (await getInvoke())(cmd, args);
}

// First-run npx/uvx MCP servers download their package on connect — allow for a
// cold download (filesystem pulls npm, fetch pulls ~45 PyPI packages) before the
// UI calls it a failure. A slow first connect is not an error.
const MCP_CONNECT_TIMEOUT_MS = 120_000;

// ── v2 daemon methods ─────────────────────────────────────────────────────────

export async function listSkills() {
    const res = await queryRpc('list_skills') as { skills?: unknown[] };
    const raw = res?.skills ?? [];
    // Daemon returns string[] (names only); mock returns full objects — normalise both.
    return raw.map(s =>
        typeof s === 'string'
            ? { name: s, description: '', category: '', lastUsed: null as string | null }
            : s as { name: string; description: string; category: string; lastUsed: string | null }
    );
}
export async function listWorkflows() {
    const res = await queryRpc('list_workflows') as { workflows?: unknown[] };
    return (res?.workflows ?? []) as Array<{ name: string; description: string; stepCount: number; lastRun: string | null; status: string }>;
}
/** Session ids from the daemon (sorted .db stems, [] if none). The UI filters out "default". */
export async function listSessions(): Promise<string[]> {
    const res = await queryRpc('list_sessions') as { sessions?: unknown[] };
    return (res?.sessions ?? []) as string[];
}
/** One model row from the daemon's list_models (free + tool-capable subset). */
export interface ModelRow { id: string; name: string; context_length: number; }
/** Live model catalog from the provider (filtered to free, tool-capable models).
 *  Falls back to the .env-declared models if the provider is unreachable, so the
 *  result is never empty. Carries model ids/names only — never an API key. */
export async function listModels(): Promise<ModelRow[]> {
    const res = await queryRpc('list_models') as { models?: unknown[] };
    return ((res?.models ?? []) as Array<{ id: string; name?: string; context_length?: number }>)
        .map(m => ({ id: m.id, name: m.name ?? m.id, context_length: m.context_length ?? 0 }));
}
export async function runWorkflow(workflow: string): Promise<void> {
    return (await getInvoke())('run_workflow', { workflow }) as Promise<void>;
}
/** One MCP server row from the daemon (Phase 1: live status + tool count). */
export interface McpServerRow {
    id: string; name: string; description: string;
    status: string; toolCount: number;
    configured: boolean; errorMessage?: string; tools?: string[];
}
/** Daemon reply to connect_mcp_server. status is 'connected' | 'needs-auth'
 *  on success; a real connect failure throws (rejected RPC) instead. */
export interface McpConnectResult {
    status: string; server: string;
    toolCount?: number; tools?: string[]; message?: string;
}

export async function listMcpServers(): Promise<McpServerRow[]> {
    const res = await queryRpc('list_mcp_servers') as { servers?: unknown[] };
    return ((res?.servers ?? []) as Array<{
        name: string; description?: string; status?: string; toolCount?: number;
        configured?: boolean; errorMessage?: string; tools?: string[];
    }>).map(s => ({
        id: s.name, name: s.name, description: s.description ?? '',
        status: (s.status ?? 'disconnected') as string, toolCount: s.toolCount ?? 0,
        configured: s.configured ?? false, errorMessage: s.errorMessage, tools: s.tools,
    }));
}
export async function connectMcpServer(server: string): Promise<McpConnectResult> {
    // queryRpc so native awaits the daemon's real response (tool count / error)
    // instead of the immediate Rust () return (#94 correlator). The long timeout
    // covers a cold npx/uvx first-run download — a slow connect is not a failure.
    return (await queryRpc('connect_mcp_server', { server }, MCP_CONNECT_TIMEOUT_MS)) as McpConnectResult;
}
export async function disconnectMcpServer(server: string): Promise<{ status: string; server: string }> {
    // Disconnect can wait on the stdio shutdown handshake; give it generous room.
    return (await queryRpc('disconnect_mcp_server', { server }, 30_000)) as { status: string; server: string };
}

// ── File system ───────────────────────────────────────────────────────────────

/**
 * Open a native OS folder picker.
 *
 * Bypass policy: the folder dialog is a system-UI action that has nothing to
 * do with agent event mocking.  When the app is running inside the Tauri
 * native shell we ALWAYS call the real Tauri command, even when VITE_MOCK=true
 * is baked in (so the rest of the UI can still run in demo/mock mode).
 *
 * Outside Tauri (browser dev, WS-proxy launcher) we fall through to the
 * generic invoke path, which in mock mode returns '/project' as a placeholder.
 */
export async function openFolder(): Promise<string | null> {
    if (isTauri) {
        // Use real native picker regardless of VITE_MOCK
        const { invoke } = await import('@tauri-apps/api/core');
        return invoke<string | null>('open_folder_dialog');
    }
    return (await getInvoke())('open_folder_dialog') as Promise<string | null>;
}
/**
 * Filesystem bypass policy (same as openFolder):
 * When running inside the Tauri native shell, always use real Tauri commands
 * regardless of VITE_MOCK — the filesystem is real, not part of agent mocking.
 * Outside Tauri (browser, WS-proxy) mock returns [] / '' as placeholders.
 */
export async function listDirectory(path: string): Promise<FileEntry[]> {
    if (isTauri) {
        const { invoke } = await import('@tauri-apps/api/core');
        return invoke<FileEntry[]>('list_directory', { path });
    }
    return (await getInvoke())('list_directory', { path }) as Promise<FileEntry[]>;
}
export async function readFile(path: string): Promise<string> {
    if (isTauri) {
        const { invoke } = await import('@tauri-apps/api/core');
        return invoke<string>('read_file', { path });
    }
    return (await getInvoke())('read_file', { path }) as Promise<string>;
}
export async function writeFile(path: string, content: string): Promise<void> {
    if (isTauri) {
        const { invoke } = await import('@tauri-apps/api/core');
        return invoke<void>('write_file', { path, content });
    }
    return (await getInvoke())('write_file', { path, content }) as Promise<void>;
}

// ── Secret storage ────────────────────────────────────────────────────────────

export async function saveApiKey(provider: string, key: string): Promise<void> {
    return (await getInvoke())('save_api_key', { provider, key }) as Promise<void>;
}
export async function getApiKey(provider: string): Promise<string | null> {
    return (await getInvoke())('get_api_key', { provider }) as Promise<string | null>;
}
/** Presence-only check: is a token saved for this provider? Returns a boolean and
 *  NEVER the secret — used to show "auth ready" for a previously-saved MCP token
 *  (incl. across sessions) without pulling the token into JS. */
export async function hasApiKey(provider: string): Promise<boolean> {
    return (await getInvoke())('has_api_key', { provider }) as Promise<boolean>;
}

// ── Event listeners ───────────────────────────────────────────────────────────

export async function onDaemonMessage(callback: (msg: HcodeMessage) => void): Promise<() => void> {
    const listen = await getListen();
    return listen('daemon-message', event => callback(event.payload as HcodeMessage));
}
export async function onDaemonStatus(callback: (info: DaemonInfo) => void): Promise<() => void> {
    const listen = await getListen();
    return listen('daemon-status', event => callback(event.payload as DaemonInfo));
}
// daemon-error is emitted ONLY by the native Tauri supervisor (daemon.rs) when a
// spawn dies before ready / times out / can't be located. The WS proxy and mock
// have no such failure path, so getListen returns their no-op listeners there and
// this simply never fires — no behaviour change off the native path.
export async function onDaemonError(callback: (err: DaemonError) => void): Promise<() => void> {
    const listen = await getListen();
    return listen('daemon-error', event => callback(event.payload as DaemonError));
}
