/**
 * AgentPanel — Right sidebar showing agent timeline, streaming output, plan, diffs, verification.
 * Phase 2 / Checkpoint 2: Added StreamingDisplay, ErrorPanel, and enhanced timeline.
 */
import React, { useState, useRef, useEffect } from 'react';
import type { AppState } from '../types';
import StreamingDisplay from './StreamingDisplay';
import ErrorPanel from './ErrorPanel';

interface AgentPanelProps {
    appState: AppState;
    onSubmitTask: (task: string, mode: 'planning' | 'fast') => Promise<void>;
    onApprovePlan: () => Promise<void>;
    onRejectPlan: () => Promise<void>;
    onSettingsClick: () => void;
    onCollapseClick: () => void;
    onReviewDiffs?: () => void;
    onAbortTask?: () => void;
    onClearError?: () => void;
}

export default function AgentPanel({
    appState,
    onSubmitTask,
    onApprovePlan,
    onRejectPlan,
    onSettingsClick,
    onCollapseClick,
    onReviewDiffs,
    onAbortTask,
    onClearError,
}: AgentPanelProps) {
    const [taskInput, setTaskInput] = useState('');
    const [mode, setMode] = useState<'planning' | 'fast'>('planning');
    const streamEndRef = useRef<HTMLDivElement>(null);
    const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());

    // Auto-scroll to bottom
    useEffect(() => {
        streamEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [appState.chatMessages, appState.logs, appState.planMarkdown, appState.streamingContent]);

    const toggleExpand = (id: string) => {
        setExpandedItems(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && e.ctrlKey) {
            e.preventDefault();
            if (taskInput.trim() && appState.phase !== 'thinking' && appState.phase !== 'executing') {
                onSubmitTask(taskInput, mode);
                setTaskInput('');
            }
        }
    };

    // Build unified stream from chat messages
    const streamItems = appState.chatMessages.map(msg => {
        if (msg.type === 'user') {
            return (
                <div key={msg.id} className="hcode-stream-entry hcode-stream-user">
                    <span className="hcode-stream-user-prefix">▹ You:</span>
                    {msg.content}
                </div>
            );
        }
        if (msg.type === 'agent_phase' && msg.phase) {
            return (
                <div key={msg.id} className="hcode-stream-phase-divider">
                    {msg.phase.charAt(0).toUpperCase() + msg.phase.slice(1)}
                </div>
            );
        }
        if (msg.type === 'system') {
            return (
                <div key={msg.id} className={`hcode-stream-entry ${msg.content.includes('❌') ? 'hcode-stream-error' : 'hcode-stream-system'}`}>
                    {msg.content}
                </div>
            );
        }
        return null;
    });

    // Timeline Logic
    const isPlan = appState.phase === 'thinking' || appState.phase === 'planning';
    const isExec = appState.phase === 'executing';
    const isVerify = appState.phase === 'verifying';
    
    const planState = (isPlan) ? 'is-active' : (['executing', 'verifying', 'done', 'error'].includes(appState.phase) ? 'is-complete' : '');
    const execState = (isExec) ? 'is-active' : (['verifying', 'done', 'error'].includes(appState.phase) ? 'is-complete' : '');
    const verifyState = (isVerify) ? 'is-active' : (['done', 'error'].includes(appState.phase) ? 'is-complete' : '');

    const getDotClass = (isActive: boolean, isComplete: boolean, phaseType: string) => {
        if (isActive) {
            if (phaseType === 'plan' && appState.phase === 'thinking') return 'is-thinking';
            if (phaseType === 'plan') return 'is-planning';
            if (phaseType === 'exec') return 'is-executing';
            if (phaseType === 'verify') return 'is-verifying';
        }
        if (isComplete) {
            if (phaseType === 'verify' && appState.phase === 'error') return 'is-error';
            return 'is-done';
        }
        return '';
    };

    const getStepIcon = (state: string, isActive: boolean) => {
        if (isActive) return '⟳';
        if (state === 'is-complete') return '●';
        return '○';
    };

    return (
        <>
            <div className="hcode-panel-header hcode-agent-header">
                <span className="hcode-agent-title">AGENT</span>

                {appState.phase !== 'idle' && (
                    <div className="hcode-phase-timeline">
                        <div className={`hcode-phase-step ${planState}`}>
                            <span className={`hcode-phase-dot ${getDotClass(isPlan, planState === 'is-complete', 'plan')}`} />
                            <span className="hcode-phase-step__label"> Plan</span>
                            {isPlan && <span className="hcode-phase-step__spinner">⟳</span>}
                        </div>
                        <div className="hcode-phase-connector" />
                        <div className={`hcode-phase-step ${execState}`}>
                            <span className={`hcode-phase-dot ${getDotClass(isExec, execState === 'is-complete', 'exec')}`} />
                            <span className="hcode-phase-step__label"> Execute</span>
                            {isExec && <span className="hcode-phase-step__spinner">⟳</span>}
                        </div>
                        <div className="hcode-phase-connector" />
                        <div className={`hcode-phase-step ${verifyState}`}>
                            <span className={`hcode-phase-dot ${getDotClass(isVerify, verifyState === 'is-complete', 'verify')}`} />
                            <span className="hcode-phase-step__label"> Verify</span>
                            {isVerify && <span className="hcode-phase-step__spinner">⟳</span>}
                        </div>
                    </div>
                )}
                <div className="hcode-agent-actions">
                    <span className="hcode-agent-icon" onClick={onSettingsClick} title="Settings (Ctrl+,)">⌘</span>
                    <span className="hcode-agent-icon" onClick={onCollapseClick} title="Collapse (Ctrl+J)">⊟</span>
                </div>
            </div>

            <div className="hcode-task-stream">
                {/* Error Panel (at top for visibility) */}
                <ErrorPanel
                    error={appState.lastError}
                    circuitBreakActive={appState.circuitBreakActive}
                    onClearError={onClearError}
                    onAbort={onAbortTask}
                />

                {/* Render Chat Stream Items */}
                {streamItems}

                {/* Streaming Display (real-time agent output) */}
                {appState.streamingContent && (
                    <StreamingDisplay
                        content={appState.streamingContent}
                        phase={appState.phase}
                    />
                )}

                {/* Inline Plan Card */}
                {appState.planMarkdown && (
                    <div className="hcode-inline-card">
                        <div className="hcode-card-header">Plan</div>
                        <div className="hcode-card-body hcode-markdown" dangerouslySetInnerHTML={{ __html: appState.planMarkdown }} />
                        {appState.phase === 'planning' && (
                            <div className="hcode-card-actions">
                                <button
                                    className="hcode-btn hcode-btn--primary hcode-btn--small"
                                    onClick={onApprovePlan}
                                >
                                    Approve
                                </button>
                                <button
                                    className="hcode-btn hcode-btn--secondary hcode-btn--small"
                                    onClick={onRejectPlan}
                                >
                                    Reject
                                </button>
                            </div>
                        )}
                    </div>
                )}

                {/* Inline Diff Card */}
                {appState.patches && appState.patches.length > 0 && (
                    <div className="hcode-inline-card">
                        <div className="hcode-card-header">
                            <span style={{ color: 'var(--semantic-warning)' }}>Changes Pending</span>
                            <span>{appState.patches.length} files</span>
                        </div>
                        <div className="hcode-card-body">
                            The agent has generated code modifications. Please review them before proceeding.
                        </div>
                        <div className="hcode-card-actions">
                            <button
                                className="hcode-btn hcode-btn--primary hcode-btn--small"
                                onClick={onReviewDiffs}
                            >
                                Review Diffs
                            </button>
                        </div>
                    </div>
                )}

                {/* Inline Verification Card */}
                {appState.verificationResult && (
                    <div className="hcode-inline-card">
                        <div className="hcode-card-header">
                            Verification: {appState.verificationResult.passed ? '● Passed' : '✕ Failed'}
                        </div>
                        <div className="hcode-card-body hcode-logs">
                            {appState.verificationResult.testResults || appState.verificationResult.markdown}
                        </div>
                        {!appState.verificationResult.passed && (
                            <div className="hcode-card-actions">
                                <button
                                    className="hcode-btn hcode-btn--primary hcode-btn--small"
                                    onClick={() => onSubmitTask("Fix the verification errors", "fast")}
                                >
                                    Fix Errors
                                </button>
                            </div>
                        )}
                    </div>
                )}

                {/* Applied patches summary */}
                {appState.appliedPatches.length > 0 && appState.phase === 'done' && (
                    <div className="hcode-inline-card">
                        <div className="hcode-card-header" style={{ color: 'var(--semantic-success)' }}>
                            ✓ {appState.appliedPatches.length} file{appState.appliedPatches.length > 1 ? 's' : ''} applied
                        </div>
                    </div>
                )}

                <div ref={streamEndRef} />
            </div>

            {/* Task Composer */}
            <div className="hcode-composer">
                <div className="hcode-composer__input-wrapper">
                    <textarea
                        className="hcode-composer__input"
                        placeholder="Describe your task..."
                        value={taskInput}
                        onChange={(e) => setTaskInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        disabled={appState.phase === 'thinking' || appState.phase === 'executing'}
                    />
                </div>
                <div className="hcode-composer-controls">
                    <div className="hcode-composer-mode">
                        <button
                            className={`hcode-mode-btn ${mode === 'planning' ? 'is-active' : ''}`}
                            onClick={() => setMode('planning')}
                        >
                            Plan
                        </button>
                        <button
                            className={`hcode-mode-btn ${mode === 'fast' ? 'is-active' : ''}`}
                            onClick={() => setMode('fast')}
                        >
                            Fast
                        </button>
                    </div>
                    <div className="hcode-composer-hint">
                        Ctrl+Enter to send
                    </div>
                </div>
            </div>
        </>
    );
}
