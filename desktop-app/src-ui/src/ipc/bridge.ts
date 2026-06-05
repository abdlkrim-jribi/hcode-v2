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
import type { HcodeMessage, FileEntry, DaemonInfo } from '../types';
import { createMockEventStream, playMockStream, agentEventToHcodeMessage } from './mock-events';

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

async function wsInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
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
        }, 30_000);
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
    { name: 'python-expert', description: 'Deep Python expertise',   category: 'Languages', lastUsed: null },
    { name: 'test-writer',   description: 'Writes pytest test suites', category: 'Testing',  lastUsed: null },
];
const MOCK_WORKFLOWS = [
    { name: 'full-feature', description: 'Plan → implement → test → document', stepCount: 4, lastRun: null, status: 'idle' },
    { name: 'quick-fix',    description: 'Patch, verify, commit',              stepCount: 3, lastRun: null, status: 'idle' },
];
const MOCK_MCP_SERVERS = [
    { id: 'filesystem', name: 'filesystem', description: 'Local file access',   status: 'disconnected', toolCount: 5 },
    { id: 'github',     name: 'github',     description: 'GitHub integration',  status: 'disconnected', toolCount: 8 },
    { id: 'brave',      name: 'brave',      description: 'Web search',          status: 'disconnected', toolCount: 1 },
];

let _mockListeners: Array<(e: { payload: unknown }) => void> = [];
let _mockCancel: (() => void) | null = null;

function _fireMock(task: string): void {
    _mockCancel?.();
    const emit = (msg: HcodeMessage) => _mockListeners.forEach(fn => fn({ payload: msg }));
    const timers: ReturnType<typeof setTimeout>[] = [];
    let t = 0;
    const after = (ms: number, fn: () => void) => timers.push(setTimeout(fn, t += ms));

    after(0,   () => emit({ type: 'planning_started',  payload: { timestamp: Date.now() } }));
    after(300, () => emit({ type: 'streaming_chunk',   payload: { content: `Analyzing: "${task}"`, phase: 'plan' } }));
    after(300, () => emit({ type: 'streaming_chunk',   payload: { content: '\nBuilding plan…', phase: 'plan' } }));
    after(400, () => emit({ type: 'plan_created',      payload: { markdown: `## Plan\n1. ${task}\n2. Verify`, taskMd: task, implementationPlanMd: `Implement: ${task}`, timestamp: Date.now() } }));
    after(300, () => emit({ type: 'execution_started', payload: { timestamp: Date.now() } }));
    after(200, () => emit({ type: 'task_update',       payload: { markdown: '**Running tool:** `write`', step: 'tool:write' } }));
    after(200, () => emit({ type: 'streaming_chunk',   payload: { content: 'Executing…', phase: 'execute' } }));
    after(200, () => emit({ type: 'task_update',       payload: { markdown: '**Tool done:** `write`', step: 'tool_result:write' } }));
    after(100, () => emit({ type: 'file_patch',        payload: { path: 'src/output.py', diff: `@@ -1,2 +1,5 @@\n+# ${task}\n+\ndef main():\n    pass`, backup: '', originalContent: 'def main():\n    pass\n', newContent: `# ${task}\n\ndef main():\n    """${task}"""\n    pass\n` } }));
    after(300, () => emit({ type: 'verification_started', payload: { timestamp: Date.now() } }));
    after(300, () => emit({ type: 'streaming_chunk',   payload: { content: 'Verifying…', phase: 'verify' } }));
    after(300, () => emit({ type: 'verification',      payload: { markdown: `**Passed.** "${task}" complete.`, passed: true, testResults: '5/5 passed' } }));
    after(200, () => emit({ type: 'done' }));

    _mockCancel = () => timers.forEach(clearTimeout);

    const legacy = playMockStream(createMockEventStream(task), ev => {
        const m = agentEventToHcodeMessage(ev);
        if (m) _mockListeners.forEach(fn => fn({ payload: m as HcodeMessage }));
    }, 600);
    const prevCancel = _mockCancel;
    _mockCancel = () => { prevCancel(); legacy(); };
}

async function mockInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
    await new Promise(r => setTimeout(r, 80));
    switch (cmd) {
        case 'start_daemon': case 'daemon_health':
            return { status: 'running', uptime: 0, pid: 0, version: '2.0.0' } satisfies DaemonInfo;
        case 'stop_daemon':       return undefined;
        case 'open_folder_dialog': return '/project';
        case 'list_directory':    return [];
        case 'read_file':         return '';
        case 'write_file':        return undefined;
        case 'save_api_key':      return undefined;
        case 'get_api_key':       return null;
        case 'list_skills':       return { skills: MOCK_SKILLS };
        case 'list_workflows':    return { workflows: MOCK_WORKFLOWS };
        case 'list_mcp_servers':  return { servers: MOCK_MCP_SERVERS };
        case 'connect_mcp_server':    return { status: 'connected',    server: args?.server ?? '' };
        case 'disconnect_mcp_server': return { status: 'disconnected', server: args?.server ?? '' };
        case 'run_workflow': { _fireMock(`run workflow ${args?.workflow ?? 'workflow'}`); return undefined; }
        case 'run_task':     { _fireMock((args?.task as string) || 'task'); return undefined; }
        case 'abort_task':        if (_mockCancel) { _mockCancel(); _mockCancel = null; } return undefined;
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

export async function runTask(task: string, mode: 'planning' | 'fast', autonomous: boolean): Promise<void> {
    return (await getInvoke())('run_task', { task, mode, autonomous }) as Promise<void>;
}
export async function abortTask(): Promise<void> {
    return (await getInvoke())('abort_task') as Promise<void>;
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

// ── v2 daemon methods ─────────────────────────────────────────────────────────

export async function listSkills() {
    const res = await (await getInvoke())('list_skills') as { skills: unknown[] };
    return (res?.skills ?? []) as Array<{ name: string; description: string; category: string; lastUsed: string | null }>;
}
export async function listWorkflows() {
    const res = await (await getInvoke())('list_workflows') as { workflows: unknown[] };
    return (res?.workflows ?? []) as Array<{ name: string; description: string; stepCount: number; lastRun: string | null; status: string }>;
}
export async function runWorkflow(workflow: string): Promise<void> {
    return (await getInvoke())('run_workflow', { workflow }) as Promise<void>;
}
export async function listMcpServers() {
    const res = await (await getInvoke())('list_mcp_servers') as { servers: unknown[] };
    return ((res?.servers ?? []) as Array<{ name: string; description?: string; status?: string; toolCount?: number }>)
        .map(s => ({ id: s.name, name: s.name, description: s.description ?? '', status: (s.status ?? 'disconnected') as string, toolCount: s.toolCount ?? 0 }));
}
export async function connectMcpServer(server: string): Promise<void> {
    await (await getInvoke())('connect_mcp_server', { server });
}
export async function disconnectMcpServer(server: string): Promise<void> {
    await (await getInvoke())('disconnect_mcp_server', { server });
}

// ── File system ───────────────────────────────────────────────────────────────

export async function openFolder(): Promise<string | null> {
    return (await getInvoke())('open_folder_dialog') as Promise<string | null>;
}
export async function listDirectory(path: string): Promise<FileEntry[]> {
    return (await getInvoke())('list_directory', { path }) as Promise<FileEntry[]>;
}
export async function readFile(path: string): Promise<string> {
    return (await getInvoke())('read_file', { path }) as Promise<string>;
}
export async function writeFile(path: string, content: string): Promise<void> {
    return (await getInvoke())('write_file', { path, content }) as Promise<void>;
}

// ── Secret storage ────────────────────────────────────────────────────────────

export async function saveApiKey(provider: string, key: string): Promise<void> {
    return (await getInvoke())('save_api_key', { provider, key }) as Promise<void>;
}
export async function getApiKey(provider: string): Promise<string | null> {
    return (await getInvoke())('get_api_key', { provider }) as Promise<string | null>;
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
