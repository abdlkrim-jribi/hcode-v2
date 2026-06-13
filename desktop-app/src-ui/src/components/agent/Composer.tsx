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
}

export default function Composer({ disabled, activeSkill, onDismissSkill, onSubmit }: Props) {
    const [taskInput, setTaskInput] = useState('');
    const [mode, setMode] = useState<'planning' | 'fast'>('planning');

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
                <div className="hcode-composer-mode">
                    <button className={`hcode-mode-btn ${mode === 'planning' ? 'is-active' : ''}`} onClick={() => setMode('planning')}>Plan</button>
                    <button className={`hcode-mode-btn ${mode === 'fast' ? 'is-active' : ''}`} onClick={() => setMode('fast')}>Fast</button>
                </div>
                <div className="hcode-composer-send">
                    <span className="hcode-composer-hint">{disabled ? 'Agent is working…' : 'Ctrl+Enter to send'}</span>
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
