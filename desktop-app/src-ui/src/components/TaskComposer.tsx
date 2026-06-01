/**
 * TaskComposer — input bar for submitting tasks to the agent.
 * Supports planning vs fast mode toggle.
 */
import React, { useState, useCallback, useRef } from 'react';
import type { AgentPhase } from '../types';

interface Props {
    phase: AgentPhase;
    onSubmit: (task: string, mode: 'planning' | 'fast') => void;
}

export default function TaskComposer({ phase, onSubmit }: Props) {
    const [task, setTask] = useState('');
    const [mode, setMode] = useState<'planning' | 'fast'>('planning');
    const inputRef = useRef<HTMLTextAreaElement>(null);

    const isBusy = phase !== 'idle' && phase !== 'done' && phase !== 'error';

    const handleSubmit = useCallback(() => {
        const trimmed = task.trim();
        if (!trimmed || isBusy) return;
        onSubmit(trimmed, mode);
        setTask('');
    }, [task, mode, isBusy, onSubmit]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
    }, [handleSubmit]);

    return (
        <div className="hcode-composer">
            <div className="hcode-composer__input-row">
                <textarea
                    ref={inputRef}
                    className="hcode-composer__input"
                    placeholder={isBusy ? 'Agent is working...' : 'Describe a task...'}
                    value={task}
                    onChange={(e) => setTask(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isBusy}
                    rows={1}
                />
                <button
                    className="hcode-btn hcode-btn--primary"
                    onClick={handleSubmit}
                    disabled={!task.trim() || isBusy}
                >
                    {isBusy ? '...' : 'Run'}
                </button>
            </div>

            <div className="hcode-composer__mode">
                <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)', cursor: 'pointer' }}>
                    <input
                        type="radio"
                        name="mode"
                        checked={mode === 'planning'}
                        onChange={() => setMode('planning')}
                        disabled={isBusy}
                    />
                    Plan
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)', cursor: 'pointer' }}>
                    <input
                        type="radio"
                        name="mode"
                        checked={mode === 'fast'}
                        onChange={() => setMode('fast')}
                        disabled={isBusy}
                    />
                    Fast
                </label>
            </div>
        </div>
    );
}
