/**
 * AgentHeader — title row + Phase 2/3 selection hooks + actions.
 *
 * The session and model dropdowns are intentionally DISABLED placeholders.
 * They establish the layout so the follow-up phases only need to wire data:
 *   • Session selection → Phase 2 (needs daemon list_sessions + thread_id +
 *     persist=True so the GUI gets real per-session memory).
 *   • Model selection   → Phase 3 (needs a daemon model API + factory override).
 * See the investigation report's effort map.
 */
import React from 'react';

interface Props {
    onNewConversation: () => void;
    onSettingsClick: () => void;
    onCollapseClick: () => void;
}

export default function AgentHeader({ onNewConversation, onSettingsClick, onCollapseClick }: Props) {
    return (
        <div className="hcode-agentchat__header">
            <div className="hcode-agentchat__headerrow">
                <span className="hcode-agent-title">AGENT</span>
                <div className="hcode-agent-actions">
                    <span className="hcode-agent-icon" onClick={onNewConversation} title="New conversation">＋</span>
                    <span className="hcode-agent-icon" onClick={onSettingsClick} title="Settings (Ctrl+,)">⌘</span>
                    <span className="hcode-agent-icon" onClick={onCollapseClick} title="Collapse (Ctrl+J)">⊟</span>
                </div>
            </div>

            <div className="hcode-agentchat__selectors">
                {/* Session selector — wired in Phase 2 (needs daemon support: list_sessions + thread_id + persist). */}
                <select
                    className="hcode-chatselect"
                    disabled
                    value="current"
                    onChange={() => { /* Phase 2 */ }}
                    title="Session switching — wired in Phase 2 (needs daemon support)"
                >
                    <option value="current">Session: current</option>
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
