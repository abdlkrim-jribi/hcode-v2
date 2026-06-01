/**
 * Shared TypeScript types for the Hcode v2 Desktop App.
 * Protocol types for IPC between React UI ↔ Tauri shell ↔ v2 Python daemon.
 *
 * v2 additions vs v1:
 *  - HcodeMessage extended with C2 streaming events emitted by bridge.py
 *  - DaemonInfo kept for mock / Tauri health response
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
    | { type: 'done' }
    // ── v2 C2 streaming events (emitted by bridge.py / mock daemon) ─────────
    | { type: 'streaming_chunk';     payload: { content: string; phase: string } }
    | { type: 'planning_started';    payload: { timestamp: number } }
    | { type: 'execution_started';   payload: { timestamp: number } }
    | { type: 'verification_started'; payload: { timestamp: number } }
    | { type: 'plan_created';        payload: { markdown: string; taskMd: string; implementationPlanMd: string; timestamp?: number } }
    | { type: 'done';                payload?: { summary?: string; timestamp?: number } };

// ── AppState ──────────────────────────────────────────────────────────────────

export interface ChatMessage {
    id: string;
    type: 'user' | 'agent' | 'system' | 'agent_phase';
    content: string;
    timestamp: string;
    phase?: AgentPhase;
}

export interface AppState {
    phase: AgentPhase;
    daemonStatus: 'unknown' | 'running' | 'error';
    workDir: string;
    fileTree: FileEntry[];
    openFilePath: string;
    openFileContent: string;
    chatMessages: ChatMessage[];
    planMarkdown: string;
    patches: FilePatchPayload[];
    appliedPatches: string[];
    verificationResult: { passed: boolean; markdown: string; testResults?: string } | null;
    logs: LogPayload[];
    error: ErrorPayload | null;
    currentTask: string;
    streamingContent: string;
    circuitBreakActive: boolean;
    lastError: string | null;
    taskHistory: string[];
}

/** Convenience alias used by StatusBar and other components. */
export type DaemonStatus = AppState['daemonStatus'];

export const INITIAL_STATE: AppState = {
    phase: 'idle',
    daemonStatus: 'unknown',
    workDir: '',
    fileTree: [],
    openFilePath: '',
    openFileContent: '',
    chatMessages: [],
    planMarkdown: '',
    patches: [],
    appliedPatches: [],
    verificationResult: null,
    logs: [],
    error: null,
    currentTask: '',
    streamingContent: '',
    circuitBreakActive: false,
    lastError: null,
    taskHistory: [],
};
