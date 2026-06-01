/**
 * PlanViewer — displays agent's generated plan with approve/reject actions.
 * Ported from VS Code extension.
 */
import React from 'react';
import type { AgentPhase } from '../types';

interface Props {
    markdown: string;
    phase: AgentPhase;
    onApprove: () => void;
    onReject: () => void;
}

export default function PlanViewer({ markdown, phase, onApprove, onReject }: Props) {
    if (!markdown) {
        return (
            <div style={{ textAlign: 'center', color: 'var(--hcode-fg-dim)', padding: '32px 16px' }}>
                <div style={{ fontSize: '32px', marginBottom: '8px' }}>📋</div>
                <div>No plan generated yet.</div>
                <div style={{ fontSize: 'var(--font-size-xs)', marginTop: '4px' }}>
                    Submit a task to generate a plan.
                </div>
            </div>
        );
    }

    const showActions = phase === 'planning';

    return (
        <div className="hcode-plan">
            <div
                className="hcode-plan__content"
                style={{
                    fontFamily: 'var(--font-sans)',
                    fontSize: 'var(--font-size-sm)',
                    lineHeight: '1.6',
                    whiteSpace: 'pre-wrap',
                }}
            >
                {markdown}
            </div>

            {showActions && (
                <div className="hcode-plan__actions">
                    <button className="hcode-btn hcode-btn--success" onClick={onApprove}>
                        ✅ Approve Plan
                    </button>
                    <button className="hcode-btn hcode-btn--danger" onClick={onReject}>
                        ❌ Reject
                    </button>
                </div>
            )}
        </div>
    );
}
