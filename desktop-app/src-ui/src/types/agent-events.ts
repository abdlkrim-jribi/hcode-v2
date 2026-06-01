/**
 * Unified event model for Hcode agent communication.
 * Used by both Desktop (Tauri) and VS Code Extension.
 * Version: 1.0.0
 *
 * This is an ADDITIVE layer — existing HcodeMessage types in types.ts remain unchanged.
 * AgentEvent provides a richer, typed contract for new integrations.
 */

// ── Phase Types ──────────────────────────────────────────────────────────────

export type AgentPhase = 'idle' | 'thinking' | 'planning' | 'executing' | 'verifying' | 'done' | 'error';

// ── Event Union ──────────────────────────────────────────────────────────────

export type AgentEvent =
  // Lifecycle events
  | { type: 'agent_ready'; timestamp: number }
  | { type: 'task_submitted'; task: string; mode: 'plan' | 'fast'; autonomous: boolean }

  // Planning phase
  | { type: 'planning_started'; timestamp: number }
  | { type: 'plan_created'; markdown: string; taskMd: string; implementationPlanMd: string }
  | { type: 'plan_approved'; timestamp: number }
  | { type: 'plan_rejected'; feedback: string; timestamp: number }

  // Execution phase
  | { type: 'execution_started'; timestamp: number }
  | { type: 'task_update'; markdown: string; step?: string }
  | { type: 'file_patch_proposed';
      path: string;
      diff: string;
      originalContent: string;
      newContent: string;
      timestamp: number;
    }
  | { type: 'file_patch_accepted'; path: string; timestamp: number }
  | { type: 'file_patch_rejected'; path: string; reason?: string; timestamp: number }
  | { type: 'rollback_requested'; timestamp: number }
  | { type: 'rollback_completed'; filesReverted: string[]; timestamp: number }

  // Verification phase
  | { type: 'verification_started'; timestamp: number }
  | { type: 'verification_completed';
      passed: boolean;
      markdown: string;
      testsRun?: number;
      testsPassed?: number;
      testsFailed?: number;
      timestamp: number;
    }

  // Progress & streaming
  | { type: 'progress'; phase: AgentPhase; message?: string }
  | { type: 'streaming_chunk'; content: string; phase: AgentPhase }

  // Error & recovery
  | { type: 'error'; message: string; code?: string; recoverable: boolean }
  | { type: 'circuit_break'; reason: string; consecutiveErrors: number }
  | { type: 'loop_detected'; message: string; count: number }

  // Daemon health
  | { type: 'daemon_health'; status: 'healthy' | 'degraded' | 'down'; uptime?: number }

  // Completion
  | { type: 'done'; summary?: string; timestamp: number };

// ── Type Guards ──────────────────────────────────────────────────────────────

/** Check if an event relates to a specific file path */
export const isFileEvent = (event: AgentEvent): event is Extract<AgentEvent, { path: string }> => {
  return 'path' in event;
};

/** Check if an event is an error-class event */
export const isErrorEvent = (
  event: AgentEvent
): event is Extract<AgentEvent, { type: 'error' | 'circuit_break' | 'loop_detected' }> => {
  return event.type === 'error' || event.type === 'circuit_break' || event.type === 'loop_detected';
};

/** Check if an event is a phase transition */
export const isPhaseEvent = (
  event: AgentEvent
): event is Extract<AgentEvent, { type: 'planning_started' | 'execution_started' | 'verification_started' | 'done' }> => {
  return ['planning_started', 'execution_started', 'verification_started', 'done'].includes(event.type);
};

// ── Validation ───────────────────────────────────────────────────────────────

/** Runtime validation — ensures raw data has a valid event shape */
export const validateAgentEvent = (data: unknown): AgentEvent | null => {
  if (!data || typeof data !== 'object' || !('type' in data)) {
    return null;
  }
  const obj = data as Record<string, unknown>;
  if (typeof obj.type !== 'string') {
    return null;
  }

  // Whitelist known event types
  const validTypes: AgentEvent['type'][] = [
    'agent_ready', 'task_submitted',
    'planning_started', 'plan_created', 'plan_approved', 'plan_rejected',
    'execution_started', 'task_update',
    'file_patch_proposed', 'file_patch_accepted', 'file_patch_rejected',
    'rollback_requested', 'rollback_completed',
    'verification_started', 'verification_completed',
    'progress', 'streaming_chunk',
    'error', 'circuit_break', 'loop_detected',
    'daemon_health', 'done',
  ];

  if (!validTypes.includes(obj.type as AgentEvent['type'])) {
    return null;
  }

  return data as AgentEvent;
};

// ── Error Severity ───────────────────────────────────────────────────────────

export type ErrorSeverity = 'warning' | 'error' | 'critical';

export const getErrorSeverity = (event: AgentEvent): ErrorSeverity | null => {
  if (event.type === 'circuit_break') return 'critical';
  if (event.type === 'loop_detected') return 'warning';
  if (event.type === 'error') return event.recoverable ? 'warning' : 'error';
  return null;
};
