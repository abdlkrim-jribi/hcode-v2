/**
 * applyAgentMessage — pure reducer from a daemon HcodeMessage to a Turn update.
 *
 * Every agent event targets the ACTIVE (last) turn. This function takes that
 * turn and returns a new turn with the event applied. It handles the full
 * event vocabulary the bridge emits — the same set cli/live.py renders:
 *
 *   planning_started / execution_started / verification_started → phase
 *   streaming_chunk{content,phase}  → plan buffer (plan phase) or live prose
 *   plan_created{markdown}          → authoritative plan
 *   task_update{markdown,step}      → activity lane (tools + lsp_verify lane)
 *   file_patch / verification       → diff card / verification card (mock extras)
 *   error / circuit_break           → single per-turn error surface
 *   done{summary}                   → final answer + terminal phase
 *
 * Markers (PLAN COMPLETE, …) are left in the raw text and stripped at render.
 */
import type { HcodeMessage, Turn, ToolActivity, AgentPhase } from '../types';

function streamPhaseToAgentPhase(p: string): AgentPhase {
    switch (p) {
        case 'plan':    return 'planning';
        case 'execute': return 'executing';
        case 'verify':  return 'verifying';
        default:        return 'executing';   // "fast" and anything else
    }
}

function newActivity(partial: Omit<ToolActivity, 'id'>): ToolActivity {
    return { id: crypto.randomUUID(), ...partial };
}

/** Mark the most recent unfinished tool with this name as done. */
function markToolDone(activities: ToolActivity[], toolName: string): ToolActivity[] {
    const next = [...activities];
    for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].kind === 'tool' && next[i].toolName === toolName && !next[i].done) {
            next[i] = { ...next[i], done: true };
            return next;
        }
    }
    return next;
}

export function applyAgentMessage(turn: Turn, msg: HcodeMessage): Turn {
    switch (msg.type) {
        // ── Phase transitions ──────────────────────────────────────────────
        case 'planning_started':
            return { ...turn, phase: 'planning' };
        case 'execution_started':
            // Accepting a reviewed plan resumes into execute — clear the prompt.
            return { ...turn, phase: 'executing', awaitingReview: false };
        case 'verification_started':
            return { ...turn, phase: 'verifying' };
        case 'agent_phase':          // v1 legacy phase event
            return { ...turn, phase: msg.phase };

        // ── Plan ───────────────────────────────────────────────────────────
        case 'plan_created':
            return { ...turn, plan: msg.payload.markdown };
        case 'plan':                 // v1 legacy
            return { ...turn, plan: msg.payload.markdown };

        // ── Streaming prose ────────────────────────────────────────────────
        case 'streaming_chunk': {
            const sp = msg.payload.phase;
            const content = msg.payload.content;
            if (sp === 'plan') {
                // Plan phase tokens type into the plan buffer until plan_created
                // replaces it with the authoritative text.
                return { ...turn, phase: 'planning', plan: turn.plan + content };
            }
            // execute / verify / fast tokens become the live answer prose.
            const phase = turn.phase === 'done' || turn.phase === 'error'
                ? turn.phase
                : streamPhaseToAgentPhase(sp);
            return { ...turn, phase, streamingContent: turn.streamingContent + content };
        }

        // ── Activity lane (tools + LSP verify) ─────────────────────────────
        case 'task_update': {
            const step = msg.payload.step ?? '';
            const label = msg.payload.markdown;
            if (step.startsWith('lsp_verify')) {
                const lspStatus = step.split(':')[1] as ToolActivity['lspStatus'];
                return {
                    ...turn,
                    activities: [...turn.activities, newActivity({
                        kind: 'lsp', label, step, lspStatus, done: lspStatus !== 'started',
                    })],
                };
            }
            if (step.startsWith('tool_result:')) {
                return { ...turn, activities: markToolDone(turn.activities, step.slice('tool_result:'.length)) };
            }
            if (step.startsWith('tool:')) {
                return {
                    ...turn,
                    activities: [...turn.activities, newActivity({
                        kind: 'tool', label, step, toolName: step.slice('tool:'.length),
                    })],
                };
            }
            return { ...turn, activities: [...turn.activities, newActivity({ kind: 'info', label, step })] };
        }

        // ── Diffs ──────────────────────────────────────────────────────────
        // Replace-on-same-path: re-proposing a file (e.g. after an LSP fix)
        // updates its patch in place instead of appending a duplicate. Without
        // this, the same path renders twice -> wrong file count + React
        // duplicate-key errors (DiffCard keys by path).
        case 'file_patch': {
            const path = msg.payload.path;
            return {
                ...turn,
                patches: [...turn.patches.filter(p => p.path !== path), msg.payload],
                appliedPatches: turn.appliedPatches.filter(p => p !== path),
                rejectedPatches: turn.rejectedPatches.filter(p => p !== path),
            };
        }

        // ── Verification (mock extra) ──────────────────────────────────────
        case 'verification':
            return {
                ...turn,
                verification: {
                    passed: msg.payload.passed,
                    markdown: msg.payload.markdown,
                    testResults: msg.payload.testResults,
                },
            };

        // ── Single error surface ───────────────────────────────────────────
        case 'error':
            return { ...turn, error: msg.payload.message, phase: 'error' };
        case 'circuit_break':
            return { ...turn, error: msg.payload.reason, phase: 'error' };

        // ── Plan review (HITL accept/reject) ────────────────────────────────────
        // Pause at the plan→execute boundary: surface the plan (if not already set
        // from plan_created) and flag the turn as awaiting a decision. The phase
        // stays 'planning' so the composer stays locked while the user decides.
        case 'plan_review':
            return {
                ...turn,
                awaitingReview: true,
                phase: 'planning',
                plan: turn.plan || msg.payload.plan || '',
            };
        // Reject → the run stopped before execute; clear the prompt, end the turn.
        case 'plan_rejected':
            return {
                ...turn,
                awaitingReview: false,
                phase: 'done',
                answer: turn.answer || (msg.payload?.message ?? 'Plan rejected — execution skipped.'),
            };

        // ── Model fallback (429 resilience) ─────────────────────────────────────
        // Surface the primary→fallback switch as an info line in the activity lane
        // so the user sees the rate-limit was handled, not a stall.
        case 'model_fallback':
            return {
                ...turn,
                activities: [...turn.activities, newActivity({
                    kind: 'info',
                    label: msg.payload.message
                        ?? `Primary rate-limited — switched to ${msg.payload.to}`,
                    step: 'model_fallback',
                })],
            };

        // ── Abort ──────────────────────────────────────────────────────────────
        // Idempotent: if ABORT_ACTIVE already set phase to 'done', this is a
        // no-op. If the daemon event arrives first (e.g. race on WS path), it
        // still lands cleanly — the partial answer is kept if one exists.
        case 'aborted':
            return {
                ...turn,
                phase: 'done',
                answer: turn.answer || (msg.payload?.message ?? '_(aborted)_'),
            };

        // ── Completion ─────────────────────────────────────────────────────
        case 'done':
            return {
                ...turn,
                phase: turn.phase === 'error' ? 'error' : 'done',
                answer: msg.payload?.summary ?? '',
            };

        // ── Ignored at turn level (handled app-level / no-op) ──────────────
        case 'ready':
        case 'log':
        default:
            return turn;
    }
}
