/**
 * PhaseTimeline — shows Plan → Execute → Verify progression.
 * Ported from VS Code extension.
 */
import React from 'react';
import type { AgentPhase } from '../types';
import { PHASE_CONFIGS } from '../types';

interface Props {
    phase: AgentPhase;
}

const STEPS: { key: AgentPhase; label: string }[] = [
    { key: 'planning', label: 'Plan' },
    { key: 'executing', label: 'Execute' },
    { key: 'verifying', label: 'Verify' },
];

const ORDER: AgentPhase[] = ['idle', 'thinking', 'planning', 'executing', 'verifying', 'done'];

export default function PhaseTimeline({ phase }: Props) {
    const phaseIdx = ORDER.indexOf(phase);

    return (
        <div className="hcode-phase-timeline">
            {STEPS.map((step, i) => {
                const stepIdx = ORDER.indexOf(step.key);
                const isDone = phaseIdx > stepIdx;
                const isActive = phase === step.key;

                return (
                    <React.Fragment key={step.key}>
                        {i > 0 && (
                            <div className={`hcode-phase-connector ${isDone ? 'hcode-phase-connector--done' : ''}`} />
                        )}
                        <div className={`hcode-phase-step ${isActive ? 'hcode-phase-step--active' : ''} ${isDone ? 'hcode-phase-step--done' : ''}`}>
                            <span>{isDone ? '✅' : isActive ? PHASE_CONFIGS[step.key].emoji : '○'}</span>
                            <span>{step.label}</span>
                        </div>
                    </React.Fragment>
                );
            })}
        </div>
    );
}
