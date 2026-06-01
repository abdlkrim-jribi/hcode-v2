/**
 * ErrorPanel — Displays errors and circuit breaker state with recovery actions.
 * Phase 2 / Checkpoint 2: Task 6 (Error & Circuit Breaker Handling).
 */
import React from 'react';

interface ErrorPanelProps {
    error: string | null;
    circuitBreakActive: boolean;
    onClearError?: () => void;
    onAbort?: () => void;
}

export default function ErrorPanel({ error, circuitBreakActive, onClearError, onAbort }: ErrorPanelProps) {
    if (!error && !circuitBreakActive) return null;

    const isCritical = circuitBreakActive;
    const borderColor = isCritical ? 'var(--semantic-error)' : 'var(--semantic-warning)';
    const bgColor = isCritical
        ? 'hsla(0, 65%, 52%, 0.08)'
        : 'hsla(38, 80%, 52%, 0.08)';
    const icon = isCritical ? '⛔' : '⚠';
    const title = isCritical ? 'Circuit Breaker Active' : 'Error';

    return (
        <div
            className="hcode-error-panel"
            style={{
                borderLeft: `3px solid ${borderColor}`,
                background: bgColor,
                padding: 'var(--space-3)',
                margin: 'var(--space-2)',
                borderRadius: 'var(--radius-sm)',
            }}
        >
            <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--space-2)',
                marginBottom: error ? 'var(--space-2)' : '0',
            }}>
                <span style={{ fontSize: 'var(--text-md)' }}>{icon}</span>
                <span style={{
                    fontSize: 'var(--text-sm)',
                    fontWeight: 600,
                    color: borderColor,
                }}>
                    {title}
                </span>
            </div>

            {error && (
                <div style={{
                    fontSize: 'var(--text-xs)',
                    color: 'var(--fg-secondary)',
                    lineHeight: 1.5,
                    marginBottom: 'var(--space-2)',
                    fontFamily: 'var(--font-mono)',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                }}>
                    {error}
                </div>
            )}

            {!error && circuitBreakActive && (
                <div style={{
                    fontSize: 'var(--text-xs)',
                    color: 'var(--fg-secondary)',
                    marginBottom: 'var(--space-2)',
                }}>
                    Too many consecutive errors. Agent paused for safety.
                </div>
            )}

            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                {onClearError && (
                    <button
                        className="hcode-btn hcode-btn--ghost hcode-btn--small"
                        onClick={onClearError}
                        title="Dismiss this error"
                    >
                        Clear
                    </button>
                )}
                {onAbort && (
                    <button
                        className="hcode-btn hcode-btn--danger hcode-btn--small"
                        onClick={onAbort}
                        title="Stop the current task"
                    >
                        Abort Task
                    </button>
                )}
            </div>
        </div>
    );
}
