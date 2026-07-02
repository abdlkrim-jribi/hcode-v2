/**
 * TurnCard — renders ONE conversation turn (Antigravity/Cursor-style).
 *
 * Layout, top → bottom:
 *   • user message bubble
 *   • per-turn PEV timeline (Plan → Execute → Verify), plan mode only
 *   • Plan panel        (from plan_created / plan-phase tokens)
 *   • Activity lane      (tool runs + the W3.3 lsp_verify started/errors/clean lane)
 *   • Live prose         (execute/verify streamed text, while the turn is active)
 *   • Diff card          (proposed file patches, reviewed in-place or in editor)
 *   • Verification card  (pass/fail + test results)
 *   • Error card         (SINGLE error surface for the turn)
 *   • Answer             (final assistant text, on done)
 *
 * Reference for the shape of a turn: src/hcode_v2/cli/live.py (LiveTurnRenderer).
 */
import React from 'react';
import type { Turn, AgentPhase } from '../../types';
import { stripMarkers } from '../../utils/markers';
import MiniMarkdown, { renderInline } from './MiniMarkdown';
import DiffCard from './DiffCard';

interface Props {
    turn: Turn;
    onReviewDiffs: (turnId: string) => void;
    onFileDecision: (turnId: string, path: string, accepted: boolean) => void;
    onDismissError: (turnId: string) => void;
    /** Plan review (HITL): accept → continue to execute; reject → stop. */
    onPlanDecision?: (turnId: string, accept: boolean) => void;
}

const BUSY_PHASES: AgentPhase[] = ['thinking', 'planning', 'executing', 'verifying'];
const PHASE_RANK: Record<AgentPhase, number> = {
    idle: 0, thinking: 1, planning: 1, executing: 2, verifying: 3, done: 4, error: 4,
};

// ── Per-turn PEV timeline ──────────────────────────────────────────────────────

function TurnTimeline({ turn }: { turn: Turn }) {
    if (turn.mode === 'fast') return null;
    const rank = PHASE_RANK[turn.phase];
    const steps: Array<{ label: string; rank: number; activeCls: string }> = [
        { label: 'Plan',    rank: 1, activeCls: 'is-planning' },
        { label: 'Execute', rank: 2, activeCls: 'is-executing' },
        { label: 'Verify',  rank: 3, activeCls: 'is-verifying' },
    ];

    return (
        <div className="hcode-phase-timeline hcode-turn__timeline">
            {steps.map((step, i) => {
                const isComplete = rank > step.rank || (turn.phase === 'done');
                const isActive = rank === step.rank && BUSY_PHASES.includes(turn.phase);
                const isError = turn.phase === 'error' && rank === step.rank;
                const dotCls = isError ? 'is-error' : isActive ? step.activeCls : isComplete ? 'is-done' : '';
                const stepCls = isActive ? 'is-active' : isComplete ? 'is-complete' : '';
                return (
                    <React.Fragment key={step.label}>
                        {i > 0 && <div className="hcode-phase-connector" />}
                        <div className={`hcode-phase-step ${stepCls}`}>
                            <span className={`hcode-phase-dot ${dotCls}`} />
                            <span className="hcode-phase-step__label"> {step.label}</span>
                            {isActive && <span className="hcode-phase-step__spinner">⟳</span>}
                        </div>
                    </React.Fragment>
                );
            })}
        </div>
    );
}

// ── Activity lane ──────────────────────────────────────────────────────────────

function ActivityLane({ turn }: { turn: Turn }) {
    if (turn.activities.length === 0) return null;
    return (
        <div className="hcode-activity">
            {turn.activities.map(a => {
                if (a.kind === 'lsp') {
                    const cls = a.lspStatus === 'errors' ? 'is-error'
                        : a.lspStatus === 'clean' ? 'is-done'
                        : 'is-verifying';
                    const glyph = a.lspStatus === 'errors' ? '⚠'
                        : a.lspStatus === 'clean' ? '✓'
                        : '🔍';
                    return (
                        <div key={a.id} className="hcode-activity__item hcode-activity__item--lsp">
                            <span className={`hcode-phase-dot ${cls}`} />
                            <span className="hcode-activity__glyph">{glyph}</span>
                            <span>{renderInline(a.label)}</span>
                        </div>
                    );
                }
                if (a.kind === 'tool') {
                    return (
                        <div key={a.id} className="hcode-activity__item">
                            <span className={`hcode-phase-dot ${a.done ? 'is-done' : 'is-executing'}`} />
                            <span>{renderInline(a.label)}</span>
                        </div>
                    );
                }
                return (
                    <div key={a.id} className="hcode-activity__item hcode-activity__item--info">
                        <span className="hcode-activity__bullet">•</span>
                        <span>{renderInline(a.label)}</span>
                    </div>
                );
            })}
        </div>
    );
}

// ── Turn ───────────────────────────────────────────────────────────────────────

export default function TurnCard({ turn, onReviewDiffs, onFileDecision, onDismissError, onPlanDecision }: Props) {
    const isBusy = BUSY_PHASES.includes(turn.phase);
    const plan = stripMarkers(turn.plan);
    const liveProse = stripMarkers(turn.streamingContent);
    const answer = turn.phase === 'done' ? (stripMarkers(turn.answer) || liveProse) : '';

    // While the turn is mid-flight, show the live prose for the current phase
    // (but not during planning — that text flows into the Plan panel).
    const showLive = isBusy && turn.phase !== 'planning' && !!liveProse;

    return (
        <div className={`hcode-turn hcode-turn--${turn.phase}`}>
            {/* User message */}
            <div className="hcode-turn__user">
                <span className="hcode-turn__avatar">You</span>
                <div className="hcode-turn__usertext">{turn.userMessage}</div>
            </div>

            {/* Agent response */}
            <div className="hcode-turn__agent">
                <TurnTimeline turn={turn} />

                {turn.phase === 'thinking' && !plan && (
                    <div className="hcode-turn__thinking">
                        <span className="hcode-phase-dot is-thinking" /> Thinking<span className="hcode-typing">…</span>
                    </div>
                )}

                {plan && (
                    <div className={`hcode-card hcode-card--plan${turn.awaitingReview ? ' hcode-card--review' : ''}`}>
                        <div className="hcode-card__title">
                            <span>Plan</span>
                            {turn.phase === 'planning' && !turn.awaitingReview && <span className="hcode-typing">typing…</span>}
                            {turn.awaitingReview && <span className="hcode-review-badge">Review required</span>}
                        </div>
                        <div className="hcode-card__body">
                            <MiniMarkdown text={plan} />
                        </div>
                        {turn.awaitingReview && (
                            <div className="hcode-plan-review">
                                <button
                                    className="hcode-btn hcode-btn--primary hcode-btn--small"
                                    onClick={() => onPlanDecision?.(turn.id, true)}
                                >
                                    Accept &amp; Run
                                </button>
                                <button
                                    className="hcode-btn hcode-btn--ghost hcode-btn--small"
                                    onClick={() => onPlanDecision?.(turn.id, false)}
                                >
                                    Reject
                                </button>
                                {/* Edit is a planned fast-follow — stubbed/disabled for now. */}
                                <button
                                    className="hcode-btn hcode-btn--ghost hcode-btn--small"
                                    disabled
                                    title="Editing the plan is coming in a follow-up"
                                >
                                    Edit…
                                </button>
                            </div>
                        )}
                    </div>
                )}

                <ActivityLane turn={turn} />

                {showLive && (
                    <div className="hcode-stream">
                        <div className="hcode-stream__label">
                            <span className={`hcode-phase-dot is-${turn.phase === 'verifying' ? 'verifying' : 'executing'}`} />
                            {turn.phase === 'verifying' ? 'Verifying' : 'Working'}
                            <span className="hcode-typing">…</span>
                        </div>
                        <MiniMarkdown text={liveProse} className="hcode-stream__text" />
                    </div>
                )}

                {(turn.patches.length > 0 || turn.appliedPatches.length > 0 || turn.rejectedPatches.length > 0) && (
                    <DiffCard
                        turnId={turn.id}
                        patches={turn.patches}
                        appliedPatches={turn.appliedPatches}
                        rejectedPatches={turn.rejectedPatches}
                        onFileDecision={onFileDecision}
                        onReviewDiffs={onReviewDiffs}
                    />
                )}

                {turn.verification && (
                    <div className={`hcode-card hcode-card--verify ${turn.verification.passed ? 'is-passed' : 'is-failed'}`}>
                        <div className="hcode-card__title">
                            {turn.verification.passed ? '✓ Verification passed' : '✕ Verification failed'}
                            {turn.verification.testResults && <span className="hcode-verify__tests">{turn.verification.testResults}</span>}
                        </div>
                        {turn.verification.markdown && (
                            <div className="hcode-card__body">
                                <MiniMarkdown text={stripMarkers(turn.verification.markdown)} />
                            </div>
                        )}
                    </div>
                )}

                {turn.error && (
                    <div className="hcode-card hcode-card--error">
                        <div className="hcode-card__title">
                            <span>⚠ Error</span>
                            <button className="hcode-card__dismiss" title="Dismiss" onClick={() => onDismissError(turn.id)}>✕</button>
                        </div>
                        <div className="hcode-card__body hcode-card__errortext">{turn.error}</div>
                    </div>
                )}

                {answer && (
                    <div className="hcode-answer">
                        <MiniMarkdown text={answer} />
                    </div>
                )}

                {turn.phase === 'done' && !answer && !turn.verification && turn.patches.length === 0 && (
                    <div className="hcode-turn__donenote">✓ Done</div>
                )}
            </div>
        </div>
    );
}
