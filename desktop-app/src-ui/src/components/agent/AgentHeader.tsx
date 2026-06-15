/**
 * AgentHeader — title row + session selector + actions.
 *
 * Phase 2 (this change): the session dropdown is now LIVE — it lists the
 * daemon's sessions (via list_sessions, "default" filtered out by App) and the
 * "+" action starts a new gui_ session. Switching sets the active thread_id that
 * App threads into every run_task, so the agent keeps that session's memory.
 * Disabled while a task is in flight (switching mid-task would mis-route events).
 *
 * The MODEL dropdown stays a disabled placeholder — Phase 3 (needs a daemon
 * model API + factory override).
 */
import React from 'react';

interface Props {
    sessions: string[];
    currentSessionId: string;
    sessionsDisabled: boolean;
    onSwitchSession: (id: string) => void;
    onNewSession: () => void;
    onSettingsClick: () => void;
    onCollapseClick: () => void;
}

/** Compact display label for a session id. */
function sessionLabel(id: string): string {
    return id.length > 22 ? id.slice(0, 21) + '…' : id;
}

export default function AgentHeader({
    sessions,
    currentSessionId,
    sessionsDisabled,
    onSwitchSession,
    onNewSession,
    onSettingsClick,
    onCollapseClick,
}: Props) {
    return (
        <div className="hcode-agentchat__header">
            <div className="hcode-agentchat__headerrow">
                <span className="hcode-agent-title">AGENT</span>
                <div className="hcode-agent-actions">
                    <span className="hcode-agent-icon" onClick={onNewSession} title="New session">＋</span>
                    <span className="hcode-agent-icon" onClick={onSettingsClick} title="Settings (Ctrl+,)">⌘</span>
                    <span className="hcode-agent-icon" onClick={onCollapseClick} title="Collapse (Ctrl+J)">⊟</span>
                </div>
            </div>

            <div className="hcode-agentchat__selectors">
                {/* Session selector — LIVE (Phase 2): lists daemon sessions, switching
                    sets the active thread_id. Disabled while a task is running. */}
                <select
                    className="hcode-chatselect"
                    value={currentSessionId}
                    disabled={sessionsDisabled}
                    onChange={e => onSwitchSession(e.target.value)}
                    title={sessionsDisabled ? 'Finish the current task to switch sessions' : 'Switch session'}
                >
                    {sessions.length === 0 && (
                        <option value={currentSessionId}>Session: {sessionLabel(currentSessionId)}</option>
                    )}
                    {sessions.map(id => (
                        <option key={id} value={id}>Session: {sessionLabel(id)}</option>
                    ))}
                </select>

                {/* Model selector — wired in Phase 3 (needs daemon model API + factory override). */}
                <select
                    className="hcode-chatselect"
                    disabled
                    value="default"
                    onChange={() => { /* Phase 3 */ }}
                    title="Model switching — wired in Phase 3 (needs daemon support)"
                >
                    <option value="default">Model: gpt-oss (default)</option>
                </select>
            </div>
        </div>
    );
}
