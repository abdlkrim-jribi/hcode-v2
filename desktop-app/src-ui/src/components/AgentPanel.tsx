/**
 * AgentPanel — the agent-chat surface.
 *
 * Thin orchestrator: header (session selector + Phase 3 model hook) → optional
 * non-destructive notice bar → scrolling conversation transcript → pinned
 * composer. Holds no agent state itself — it renders the turns[] for the active
 * session and forwards user intents (submit, session switch/new) up to App.
 */
import React from 'react';
import type { Turn } from '../types';
import AgentHeader from './agent/AgentHeader';
import ConversationView from './agent/ConversationView';
import Composer from './agent/Composer';

interface AgentPanelProps {
    turns: Turn[];
    /** True while the active (last) turn is mid-flight — locks Run + session switch. */
    isBusy: boolean;
    /** Sessions for the dropdown (active first, "default" already filtered). */
    sessions: string[];
    currentSessionId: string;
    onSwitchSession: (id: string) => void;
    onNewSession: () => void;
    /** Transient non-destructive notice (e.g. single-flight) — not a turn error. */
    notice: string | null;
    onDismissNotice: () => void;
    onSubmitTask: (task: string, mode: 'planning' | 'fast') => void;
    onReviewDiffs: (turnId: string) => void;
    onFileDecision: (turnId: string, path: string, accepted: boolean) => void;
    onDismissError: (turnId: string) => void;
    onSettingsClick: () => void;
    onCollapseClick: () => void;
    /** Skill selected from the Skills panel — shown as a chip in the composer. */
    activeSkill?: string | null;
    onDismissSkill?: () => void;
}

export default function AgentPanel({
    turns,
    isBusy,
    sessions,
    currentSessionId,
    onSwitchSession,
    onNewSession,
    notice,
    onDismissNotice,
    onSubmitTask,
    onReviewDiffs,
    onFileDecision,
    onDismissError,
    onSettingsClick,
    onCollapseClick,
    activeSkill,
    onDismissSkill,
}: AgentPanelProps) {
    return (
        <div className="hcode-agentchat">
            <AgentHeader
                sessions={sessions}
                currentSessionId={currentSessionId}
                sessionsDisabled={isBusy}
                onSwitchSession={onSwitchSession}
                onNewSession={onNewSession}
                onSettingsClick={onSettingsClick}
                onCollapseClick={onCollapseClick}
            />
            {notice && (
                <div className="hcode-agent-notice">
                    <span>{notice}</span>
                    <button className="hcode-agent-notice__x" onClick={onDismissNotice} title="Dismiss">✕</button>
                </div>
            )}
            <ConversationView
                turns={turns}
                onReviewDiffs={onReviewDiffs}
                onFileDecision={onFileDecision}
                onDismissError={onDismissError}
            />
            <Composer
                disabled={isBusy}
                activeSkill={activeSkill}
                onDismissSkill={onDismissSkill}
                onSubmit={onSubmitTask}
            />
        </div>
    );
}
