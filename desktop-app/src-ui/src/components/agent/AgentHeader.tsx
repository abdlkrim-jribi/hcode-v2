/**
 * AgentHeader — title row + session selector + actions.
 *
 * Phase 2 (this change): the session dropdown is now LIVE — it lists the
 * daemon's sessions (via list_sessions, "default" filtered out by App) and the
 * "+" action starts a new gui_ session. Switching sets the active thread_id that
 * App threads into every run_task, so the agent keeps that session's memory.
 * Disabled while a task is in flight (switching mid-task would mis-route events).
 *
 * The MODEL dropdown is LIVE (Phase 3): it lists the provider's free, tool-capable
 * models (fetched by App via list_models) and sets the model the next run uses.
 * The empty value = the .env default model. Disabled while a task is in flight.
 */
import React, { useState, useRef, useEffect } from 'react';
import type { ModelRow } from '../../ipc/bridge';

interface Props {
    sessions: string[];
    currentSessionId: string;
    /** Friendly display names keyed by thread_id; falls back to the id. */
    sessionNames: Record<string, string>;
    sessionsDisabled: boolean;
    onSwitchSession: (id: string) => void;
    onNewSession: () => void;
    onRenameSession: (id: string, name: string) => void;
    onSettingsClick: () => void;
    onCollapseClick: () => void;
    /** Live model catalog (free + tool-capable). Empty while loading/failed. */
    models: ModelRow[];
    /** True while the model list is being fetched. */
    modelsLoading: boolean;
    /** Selected model id, or null = the .env default model. */
    selectedModel: string | null;
    /** Pick a model id, or null to revert to the default. */
    onSelectModel: (id: string | null) => void;
    /** Plan review (HITL) per-session toggle — default off. */
    planReview: boolean;
    onTogglePlanReview: (on: boolean) => void;
}

/** Compact display label for a session id. */
function sessionLabel(id: string): string {
    return id.length > 22 ? id.slice(0, 21) + '…' : id;
}

export default function AgentHeader({
    sessions,
    currentSessionId,
    sessionNames,
    sessionsDisabled,
    onSwitchSession,
    onNewSession,
    onRenameSession,
    onSettingsClick,
    onCollapseClick,
    models,
    modelsLoading,
    selectedModel,
    onSelectModel,
    planReview,
    onTogglePlanReview,
}: Props) {
    // Inline rename of the active session. The thread_id is unchanged — only the
    // label — so daemon memory is unaffected.
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    // Friendly label for a session id: custom name if set, else the id label.
    const displayLabel = (id: string): string => sessionNames[id]?.trim() || sessionLabel(id);

    const startEditing = () => {
        setDraft(sessionNames[currentSessionId] ?? '');
        setEditing(true);
    };
    const commit = () => {
        onRenameSession(currentSessionId, draft);
        setEditing(false);
    };
    const cancel = () => setEditing(false);

    useEffect(() => {
        if (editing) { inputRef.current?.focus(); inputRef.current?.select(); }
    }, [editing]);
    // Leaving/​switching the active session ends any in-progress rename cleanly.
    useEffect(() => { setEditing(false); }, [currentSessionId]);

    return (
        <div className="hcode-agentchat__header">
            <div className="hcode-agentchat__headerrow">
                <span className="hcode-agent-title">AGENT</span>
                <div className="hcode-agent-actions">
                    <span className="hcode-agent-icon" onClick={onNewSession} title="New session">＋</span>
                    <span className="hcode-agent-icon" onClick={startEditing} title="Rename session">✎</span>
                    <span className="hcode-agent-icon" onClick={onSettingsClick} title="Settings (Ctrl+,)">⌘</span>
                    <span className="hcode-agent-icon" onClick={onCollapseClick} title="Collapse (Ctrl+J)">⊟</span>
                </div>
            </div>

            <div className="hcode-agentchat__selectors">
                {/* Session selector — LIVE (Phase 2): lists daemon sessions, switching
                    sets the active thread_id. Disabled while a task is running.
                    While renaming, the select is swapped for an inline input that
                    edits the active session's DISPLAY NAME (the id is unchanged). */}
                {editing ? (
                    <input
                        ref={inputRef}
                        className="hcode-chatselect hcode-chatselect--rename"
                        value={draft}
                        placeholder={sessionLabel(currentSessionId)}
                        onChange={e => setDraft(e.target.value)}
                        onBlur={commit}
                        onKeyDown={e => {
                            if (e.key === 'Enter') { e.preventDefault(); commit(); }
                            else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
                        }}
                        title="Rename session — Enter to save, Esc to cancel"
                        aria-label="Rename session"
                    />
                ) : (
                    <select
                        className="hcode-chatselect"
                        value={currentSessionId}
                        disabled={sessionsDisabled}
                        onChange={e => onSwitchSession(e.target.value)}
                        title={sessionsDisabled ? 'Finish the current task to switch sessions' : 'Switch session'}
                    >
                        {sessions.length === 0 && (
                            <option value={currentSessionId}>Session: {displayLabel(currentSessionId)}</option>
                        )}
                        {sessions.map(id => (
                            <option key={id} value={id}>Session: {displayLabel(id)}</option>
                        ))}
                    </select>
                )}

                {/* Model selector — LIVE (Phase 3): the provider's free, tool-capable
                    models. Empty value = the .env default model. The chosen id is
                    sent on the next run_task; switching evicts the cached agent so
                    the next task uses the new model. Disabled while a task runs. */}
                <select
                    className="hcode-chatselect"
                    value={selectedModel ?? ''}
                    disabled={sessionsDisabled || modelsLoading}
                    onChange={e => onSelectModel(e.target.value || null)}
                    title={
                        modelsLoading ? 'Loading available models…'
                        : sessionsDisabled ? 'Finish the current task to switch models'
                        : 'Choose the model for the next task (free, tool-capable)'
                    }
                >
                    <option value="">
                        {modelsLoading ? 'Model: loading…' : 'Model: default (.env)'}
                    </option>
                    {models.map(m => (
                        <option key={m.id} value={m.id}>Model: {m.name}</option>
                    ))}
                </select>

                {/* Plan review (HITL) toggle — per session, default off. When on,
                    the next run pauses at the plan→execute boundary for accept/reject.
                    Locked while a task runs (can't flip mid-run). */}
                <label
                    className="hcode-planreview-toggle"
                    title="Pause after planning to accept or reject before the agent executes"
                >
                    <input
                        type="checkbox"
                        checked={planReview}
                        disabled={sessionsDisabled}
                        onChange={e => onTogglePlanReview(e.target.checked)}
                    />
                    <span>Review plan</span>
                </label>
            </div>
        </div>
    );
}
