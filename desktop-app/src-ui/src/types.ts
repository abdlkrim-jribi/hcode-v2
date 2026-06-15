/**
 * Shared TypeScript types for the Hcode v2 Desktop App.
 * Protocol types for IPC between React UI ↔ Tauri shell ↔ v2 Python daemon.
 *
 * v3 (agent-chat rebuild):
 *  - AppState is now a CONVERSATION of `turns[]`, not a single flat task.
 *    Each task the user submits APPENDS a Turn; prior turns are never wiped.
 *    All agent output (plan, streamed text, diffs, verification, errors) is
 *    scoped to the turn it belongs to — see `Turn` below.
 *  - HcodeMessage (the wire protocol) is unchanged: bridge.py / the mock
 *    daemon still emit the same events; the UI just routes them per-turn.
 */

// ── File System ────────────────────────────────────────────────────────────

export interface FileEntry {
    name: string;
    path: string;
    isDirectory: boolean;
    children?: FileEntry[];
}

// ── Daemon info ───────────────────────────────────────────────────────────

export interface DaemonInfo {
    status: 'running' | 'error';
    uptime?: number;
    pid?: number;
    version?: string;
}

// ── Agent Phase System ────────────────────────────────────────────────────────

export type AgentPhase = 'idle' | 'thinking' | 'planning' | 'executing' | 'verifying' | 'done' | 'error';

export interface PhaseConfig {
    label: string;
    color: string;
    cssVar: string;
    emoji: string;
}

export const PHASE_CONFIGS: Record<AgentPhase, PhaseConfig> = {
    idle:      { label: 'Idle',      color: 'muted',   cssVar: '--hcode-fg-muted',  emoji: '○'  },
    thinking:  { label: 'Thinking',  color: 'purple',  cssVar: '--phase-thinking',  emoji: '🟣' },
    planning:  { label: 'Planning',  color: 'blue',    cssVar: '--phase-planning',   emoji: '🔵' },
    executing: { label: 'Executing', color: 'amber',   cssVar: '--phase-executing',  emoji: '🟡' },
    verifying: { label: 'Verifying', color: 'green',   cssVar: '--phase-verifying',  emoji: '🟢' },
    done:      { label: 'Done',      color: 'green',   cssVar: '--hcode-success',    emoji: '✅' },
    error:     { label: 'Error',     color: 'red',     cssVar: '--hcode-error',      emoji: '❌' },
};

// ── IPC Payload Types ─────────────────────────────────────────────────────────

export interface PlanPayload         { markdown: string }
export interface TaskUpdatePayload   { markdown: string; step?: string }
export interface FilePatchPayload    {
    path: string; diff: string; backup: string;
    originalContent: string; newContent: string; aiExplanation?: string;
}
export interface VerificationPayload { markdown: string; passed: boolean; testResults?: string }
export interface ErrorPayload        { message: string; suggestion: string }
export interface CircuitBreakPayload { reason: string }
export interface LogPayload          { line: string; stream: 'stdout' | 'stderr' }

// ── HcodeMessage — wire protocol (v1 legacy + v2 C2 streaming events) ─────────
// Unchanged contract — the new chat UI must keep handling ALL of these.

export type HcodeMessage =
    // ── v1 legacy events ────────────────────────────────────────────────────
    | { type: 'plan';          payload: PlanPayload }
    | { type: 'task_update';   payload: TaskUpdatePayload }
    | { type: 'file_patch';    payload: FilePatchPayload }
    | { type: 'verification';  payload: VerificationPayload }
    | { type: 'error';         payload: ErrorPayload }
    | { type: 'circuit_break'; payload: CircuitBreakPayload }
    | { type: 'log';           payload: LogPayload }
    | { type: 'agent_phase';   phase: AgentPhase }
    | { type: 'ready' }
    // ── v2 C2 streaming events (emitted by bridge.py / mock daemon) ─────────
    | { type: 'streaming_chunk';     payload: { content: string; phase: string } }
    | { type: 'planning_started';    payload: { timestamp: number } }
    | { type: 'execution_started';   payload: { timestamp: number } }
    | { type: 'verification_started'; payload: { timestamp: number } }
    | { type: 'plan_created';        payload: { markdown: string; taskMd: string; implementationPlanMd: string; timestamp?: number } }
    | { type: 'done';                payload?: { summary?: string; timestamp?: number } };

// ── Conversation model — Turn ──────────────────────────────────────────────────

/** One unit of work inside a turn: a tool run, an LSP check, or an info line. */
export interface ToolActivity {
    id: string;
    kind: 'tool' | 'lsp' | 'info';
    /** Display text (already human-readable; may contain markdown emphasis). */
    label: string;
    /** Raw `step` from the daemon, e.g. "tool:write" / "lsp_verify:clean". */
    step?: string;
    /** Tool name, parsed from `tool:<name>` — used to mark completion. */
    toolName?: string;
    /** W3.3 LSP Verify lane status. */
    lspStatus?: 'started' | 'errors' | 'clean';
    /** True once the matching tool_result / terminal lsp status arrived. */
    done?: boolean;
}

export interface TurnVerification {
    passed: boolean;
    markdown: string;
    testResults?: string;
}

/**
 * A single conversation turn: the user's message plus everything the agent did
 * in response. A new submit APPENDS one of these; it is never cleared by the
 * next submit. All fields below are owned by THIS turn.
 */
export interface Turn {
    id: string;
    userMessage: string;
    mode: 'planning' | 'fast';
    /** Current phase of this turn (drives the per-turn PEV timeline + composer lock). */
    phase: AgentPhase;
    /** Plan markdown (authoritative once plan_created arrives; raw — strip at render). */
    plan: string;
    /** Live assistant prose for execute/verify phases (raw — strip markers at render). */
    streamingContent: string;
    /** Tool / todo / LSP progress lane. */
    activities: ToolActivity[];
    /** Diffs proposed in this turn, pending review. */
    patches: FilePatchPayload[];
    /** Paths the user accepted. */
    appliedPatches: string[];
    /** Paths the user rejected. */
    rejectedPatches: string[];
    /** Verification result for this turn. */
    verification: TurnVerification | null;
    /** Final answer text, set on `done` (raw — strip at render). */
    answer: string;
    /** SINGLE error surface for this turn (no more triple-render). */
    error: string | null;
    createdAt: string;
}

// ── AppState ──────────────────────────────────────────────────────────────────

export interface AppState {
    daemonStatus: 'unknown' | 'running' | 'error';
    workDir: string;
    fileTree: FileEntry[];
    openFilePath: string;
    openFileContent: string;
    /** The conversation: an ordered list of turns. The last turn is "active". */
    turns: Turn[];
    /** Which turn's diffs are open in the center editor panel (Monaco review). */
    reviewTurnId: string | null;
    /** Flat history of submitted task strings (for future composer recall). */
    taskHistory: string[];

    // ── Sessions (Phase 2) ──────────────────────────────────────────────────
    /** Active session = the thread_id sent on every run_task (the agent-memory key). */
    currentSessionId: string;
    /** Session ids from the daemon (list_sessions). "default" is filtered out in the UI. */
    sessions: string[];
    /**
     * In-run transcripts for non-active sessions, so switching back restores them.
     * In-memory only: there is NO daemon history API, so selecting a session with no
     * in-run turns (a prior-run or CLI-created session) opens an empty transcript even
     * though the agent still remembers it server-side. See PR / report for scope.
     */
    archivedTurns: Record<string, Turn[]>;
}

/** Convenience alias used by StatusBar and other components. */
export type DaemonStatus = AppState['daemonStatus'];

export const INITIAL_STATE: AppState = {
    daemonStatus: 'unknown',
    workDir: '',
    fileTree: [],
    openFilePath: '',
    openFileContent: '',
    turns: [],
    reviewTurnId: null,
    taskHistory: [],
    currentSessionId: '',
    sessions: [],
    archivedTurns: {},
};

/** A fresh turn for a newly-submitted task. */
export function createTurn(userMessage: string, mode: 'planning' | 'fast'): Turn {
    return {
        id: crypto.randomUUID(),
        userMessage,
        mode,
        phase: 'thinking',
        plan: '',
        streamingContent: '',
        activities: [],
        patches: [],
        appliedPatches: [],
        rejectedPatches: [],
        verification: null,
        answer: '',
        error: null,
        createdAt: new Date().toISOString(),
    };
}
