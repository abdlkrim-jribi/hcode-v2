/**
 * IPC Bridge — v2 edition.
 *
 * Abstracts communication between the React UI and the v2 Python daemon.
 * Two modes:
 *   VITE_MOCK=true  (default) — all calls answered by mock fixtures;
 *                               no Tauri or live daemon needed.
 *   VITE_MOCK=false           — calls forwarded to Tauri invoke/listen,
 *                               which C4 (Tauri shell) will route to the
 *                               Python daemon's JSON-RPC stdio transport.
 *
 * v2 additions vs v1:
 *   listSkills / listWorkflows / runWorkflow         (C1 daemon methods)
 *   listMcpServers / connectMcpServer / disconnectMcpServer
 *   Mock updated to emit C2 streaming events:
 *     streaming_chunk, task_update, planning_started,
 *     execution_started, verification_started, plan_created
 */
import type { HcodeMessage, FileEntry, DaemonInfo } from '../types';
import { createMockEventStream, playMockStream, agentEventToHcodeMessage } from './mock-events';

// ── Mock-mode gate ────────────────────────────────────────────────────────────

const MOCK_MODE: boolean =
    typeof import.meta !== 'undefined' &&
    (import.meta as ImportMeta).env?.VITE_MOCK === 'true';

// ── Tauri detection ───────────────────────────────────────────────────────────

const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
type ListenFn = (event: string, handler: (e: { payload: unknown }) => void) => Promise<() => void>;

let tauriInvoke: InvokeFn | null = null;
let tauriListen: ListenFn | null = null;

async function getInvoke(): Promise<InvokeFn> {
    if (MOCK_MODE) return mockInvoke;
    if (!isTauri) throw new Error('[IPC] Not in Tauri and VITE_MOCK is not "true".');
    if (!tauriInvoke) {
        const { invoke } = await import('@tauri-apps/api/core');
        tauriInvoke = invoke as InvokeFn;
    }
    return tauriInvoke!;
}

async function getListen(): Promise<ListenFn> {
    if (MOCK_MODE) return mockListen;
    if (!isTauri) throw new Error('[IPC] Not in Tauri and VITE_MOCK is not "true".');
    if (!tauriListen) {
        const { listen } = await import('@tauri-apps/api/event');
        tauriListen = listen as unknown as ListenFn;
    }
    return tauriListen!;
}

if (MOCK_MODE) console.warn('[IPC] VITE_MOCK=true — using mock daemon');

// ── Mock fixtures ─────────────────────────────────────────────────────────────

const MOCK_SKILLS = [
    { name: 'python-expert', description: 'Deep Python expertise', category: 'Languages', lastUsed: null },
    { name: 'test-writer',   description: 'Writes pytest suites',  category: 'Testing',   lastUsed: null },
];

const MOCK_WORKFLOWS = [
    { name: 'full-feature', description: 'Plan → implement → test → document', stepCount: 4, lastRun: null, status: 'idle' as const },
    { name: 'quick-fix',    description: 'Patch, verify, commit',              stepCount: 3, lastRun: null, status: 'idle' as const },
];

const MOCK_MCP_SERVERS = [
    { id: 'filesystem', name: 'filesystem', description: 'Local file access',     status: 'disconnected' as const, toolCount: 5 },
    { id: 'github',     name: 'github',     description: 'GitHub integration',    status: 'disconnected' as const, toolCount: 8 },
    { id: 'brave',      name: 'brave',      description: 'Web search via Brave',  status: 'disconnected' as const, toolCount: 1 },
];

let mockDaemonListeners: Array<(e: { payload: unknown }) => void> = [];
let mockCancelStream: (() => void) | null = null;

async function mockInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
    await new Promise(r => setTimeout(r, 80));
    switch (cmd) {
        case 'start_daemon':
        case 'daemon_health':
            return { status: 'running', uptime: 0, pid: 0, version: '2.0.0' } satisfies DaemonInfo;
        case 'stop_daemon':      return undefined;
        case 'open_folder_dialog': return '/project';
        case 'list_directory':   return [];
        case 'read_file':        return '';
        case 'write_file':       return undefined;
        case 'save_api_key':     return undefined;
        case 'get_api_key':      return null;

        // ── v2 daemon methods ─────────────────────────────────────────────
        case 'list_skills':    return { skills: MOCK_SKILLS };
        case 'list_workflows': return { workflows: MOCK_WORKFLOWS };
        case 'list_mcp_servers': return { servers: MOCK_MCP_SERVERS };
        case 'connect_mcp_server':
            return { status: 'connected', server: (args?.server as string) ?? '' };
        case 'disconnect_mcp_server':
            return { status: 'disconnected', server: (args?.server as string) ?? '' };
        case 'run_workflow': {
            const name = (args?.workflow as string) ?? 'workflow';
            _fireMockStream(`run workflow ${name}`);
            return undefined;
        }

        case 'run_task': {
            const task = (args?.task as string) || 'Demo task';
            _fireMockStream(task);
            return undefined;
        }
        case 'abort_task':
            if (mockCancelStream) { mockCancelStream(); mockCancelStream = null; }
            return undefined;
        case 'approve_plan': case 'reject_plan':
        case 'accept_patch':  case 'reject_patch': case 'rollback_all':
            return undefined;
        default:
            console.warn(`[Mock IPC] Unknown command: ${cmd}`);
            return undefined;
    }
}

function _fireMockStream(task: string): void {
    if (mockCancelStream) mockCancelStream();
    // Emit C2-style events directly so the App.tsx v2 handler fires
    const emit = (msg: HcodeMessage) => mockDaemonListeners.forEach(fn => fn({ payload: msg }));
    const delay = (ms: number) => new Promise<void>(r => setTimeout(r, ms));
    const timers: ReturnType<typeof setTimeout>[] = [];

    let i = 0;
    const schedule = (ms: number, fn: () => void) => {
        timers.push(setTimeout(fn, ms));
    };

    schedule(i += 0,   () => emit({ type: 'planning_started', payload: { timestamp: Date.now() } }));
    schedule(i += 300, () => emit({ type: 'streaming_chunk', payload: { content: `Analyzing: "${task}"`, phase: 'plan' } }));
    schedule(i += 300, () => emit({ type: 'streaming_chunk', payload: { content: '\nBuilding plan...', phase: 'plan' } }));
    schedule(i += 400, () => emit({ type: 'plan_created', payload: {
        markdown: `## Plan\n1. Understand the task: *${task}*\n2. Apply changes\n3. Verify`,
        taskMd: task,
        implementationPlanMd: `Implement: ${task}`,
        timestamp: Date.now(),
    }}));
    schedule(i += 300, () => emit({ type: 'execution_started', payload: { timestamp: Date.now() } }));
    schedule(i += 200, () => emit({ type: 'task_update', payload: { markdown: '**Running tool:** `write`', step: 'tool:write' } }));
    schedule(i += 200, () => emit({ type: 'streaming_chunk', payload: { content: `Executing task...`, phase: 'execute' } }));
    schedule(i += 200, () => emit({ type: 'task_update', payload: { markdown: '**Tool done:** `write`', step: 'tool_result:write' } }));
    // Emit a diff card
    schedule(i += 100, () => emit({ type: 'file_patch', payload: {
        path: `src/output.py`,
        diff: `@@ -1,2 +1,5 @@\n+# ${task}\n+\n def main():\n     pass`,
        backup: '',
        originalContent: 'def main():\n    pass\n',
        newContent: `# ${task}\n\ndef main():\n    """${task}"""\n    pass\n`,
    }}));
    schedule(i += 300, () => emit({ type: 'verification_started', payload: { timestamp: Date.now() } }));
    schedule(i += 300, () => emit({ type: 'streaming_chunk', payload: { content: 'Verifying...', phase: 'verify' } }));
    schedule(i += 300, () => emit({ type: 'verification', payload: {
        markdown: `**Verification passed.** Task "${task}" complete.`,
        passed: true,
        testResults: '5/5 passed',
    }}));
    schedule(i += 200, () => emit({ type: 'done' }));

    mockCancelStream = () => timers.forEach(clearTimeout);

    // Also run the legacy AgentEvent stream so AgentChat gets messages
    const events = createMockEventStream(task);
    const legacyCancel = playMockStream(events, (event) => {
        const msg = agentEventToHcodeMessage(event);
        if (msg) mockDaemonListeners.forEach(fn => fn({ payload: msg as HcodeMessage }));
    }, 600);
    const prevCancel = mockCancelStream;
    mockCancelStream = () => { prevCancel(); legacyCancel(); };
}

async function mockListen(
    event: string,
    handler: (e: { payload: unknown }) => void,
): Promise<() => void> {
    if (event === 'daemon-message') {
        mockDaemonListeners.push(handler);
        return () => { mockDaemonListeners = mockDaemonListeners.filter(fn => fn !== handler); };
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

// ── v2 daemon methods (C1 JSON-RPC) ──────────────────────────────────────────

export async function listSkills(): Promise<Array<{ name: string; description: string; category: string; lastUsed: string | null }>> {
    const res = await (await getInvoke())('list_skills') as { skills: Array<{ name: string; description: string; category: string; lastUsed: string | null }> };
    return res?.skills ?? [];
}

export async function listWorkflows(): Promise<Array<{ name: string; description: string; stepCount: number; lastRun: string | null; status: string }>> {
    const res = await (await getInvoke())('list_workflows') as { workflows: Array<{ name: string; description: string; stepCount: number; lastRun: string | null; status: string }> };
    return res?.workflows ?? [];
}

export async function runWorkflow(workflow: string): Promise<void> {
    return (await getInvoke())('run_workflow', { workflow }) as Promise<void>;
}

export async function listMcpServers(): Promise<Array<{ id: string; name: string; description: string; status: string; toolCount: number }>> {
    const res = await (await getInvoke())('list_mcp_servers') as { servers: Array<{ id: string; name: string; description: string; status: string; toolCount: number }> };
    return (res?.servers ?? []).map(s => ({ id: s.name, name: s.name, description: s.description ?? '', status: 'disconnected' as const, toolCount: 0 }));
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
    return listen('daemon-message', (event) => callback(event.payload as HcodeMessage));
}

export async function onDaemonStatus(callback: (info: DaemonInfo) => void): Promise<() => void> {
    const listen = await getListen();
    return listen('daemon-status', (event) => callback(event.payload as DaemonInfo));
}
