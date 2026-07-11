/**
 * Composer — the message box, pinned to the bottom of the conversation.
 * Plan/Fast mode toggle, active-skill chip, Ctrl/Cmd+Enter to send.
 *
 * The Run button is disabled while the active turn is mid-flight, but the
 * textarea stays editable so the next message can be drafted. On error the
 * turn is no longer "busy", so the composer re-enables immediately — no
 * resubmit graveyard.
 */
import React, { useState } from 'react';

interface Props {
    /** True while the active turn is thinking/planning/executing/verifying. */
    disabled: boolean;
    activeSkill?: string | null;
    onDismissSkill?: () => void;
    onSubmit: (task: string, mode: 'planning' | 'fast') => void;
    onAbort?: () => void;
    /** Controlled per-session mode: Plan forces the full plan→execute→verify arc;
     *  Fast leaves PEV's classifier to decide (single-pass for ordinary tasks). */
    mode: 'planning' | 'fast';
    onModeChange: (mode: 'planning' | 'fast') => void;
}

export default function Composer({ disabled, activeSkill, onDismissSkill, onSubmit, onAbort, mode, onModeChange }: Props) {
    const [taskInput, setTaskInput] = useState('');

    const submit = () => {
        const trimmed = taskInput.trim();
        if (!trimmed || disabled) return;
        const text = activeSkill ? `${trimmed}\n\n[Preferred skill: ${activeSkill}]` : trimmed;
        onSubmit(text, mode);
        setTaskInput('');
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            submit();
        }
    };

    return (
        <div className="hcode-composer">
            {activeSkill && (
                <div className="hcode-skillchip">
                    <span>🎯</span>
                    <span className="hcode-skillchip__name">Skill: <strong>{activeSkill}</strong></span>
                    <button className="hcode-skillchip__x" onClick={onDismissSkill} title="Remove active skill">✕</button>
                </div>
            )}

            <div className="hcode-composer__input-wrapper">
                <textarea
                    className="hcode-composer__input"
                    placeholder={activeSkill ? `Task for skill "${activeSkill}"…` : 'Describe a task for the agent…'}
                    value={taskInput}
                    onChange={e => setTaskInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                />
            </div>

            <div className="hcode-composer-controls">
                <div className="hcode-composer-mode" role="radiogroup" aria-label="Run mode">
                    <button
                        className={`hcode-mode-btn ${mode === 'planning' ? 'is-active' : ''}`}
                        role="radio"
                        aria-checked={mode === 'planning'}
                        title="Plan: run the full plan → execute → verify arc (with a language-server type check) for any task."
                        onClick={() => onModeChange('planning')}
                    >
                        Plan
                    </button>
                    <button
                        className={`hcode-mode-btn ${mode === 'fast' ? 'is-active' : ''}`}
                        role="radio"
                        aria-checked={mode === 'fast'}
                        title="Fast: single-pass. PEV's classifier decides — ordinary tasks skip planning and verification."
                        onClick={() => onModeChange('fast')}
                    >
                        Fast
                    </button>
                </div>
                <div className="hcode-composer-send">
                    <span className="hcode-composer-hint">{disabled ? 'Agent is working…' : 'Ctrl+Enter to send'}</span>
                    {disabled && onAbort && (
                        <button
                            className="hcode-btn hcode-btn--ghost hcode-btn--small"
                            onClick={onAbort}
                            title="Stop the current task"
                        >
                            Stop
                        </button>
                    )}
                    <button
                        className="hcode-btn hcode-btn--primary hcode-btn--small"
                        onClick={submit}
                        disabled={disabled || !taskInput.trim()}
                    >
                        {disabled ? 'Running…' : 'Run'}
                    </button>
                </div>
            </div>
        </div>
    );
}
