/**
 * AgentPanel — the agent-chat surface (rebuilt).
 *
 * Thin orchestrator: header (with Phase 2/3 session/model hooks) → scrolling
 * conversation transcript of turn cards → pinned composer. All per-turn PEV
 * rendering lives in components/agent/*. The panel holds no agent state itself
 * — it renders the turns[] it is given and forwards user intents up to App.
 */
import React from 'react';
import type { Turn } from '../types';
import AgentHeader from './agent/AgentHeader';
import ConversationView from './agent/ConversationView';
import Composer from './agent/Composer';

interface AgentPanelProps {
    turns: Turn[];
    /** True while the active (last) turn is mid-flight — locks the Run button. */
    isBusy: boolean;
    onSubmitTask: (task: string, mode: 'planning' | 'fast') => void;
    onReviewDiffs: (turnId: string) => void;
    onFileDecision: (turnId: string, path: string, accepted: boolean) => void;
    onDismissError: (turnId: string) => void;
    onNewConversation: () => void;
    onSettingsClick: () => void;
    onCollapseClick: () => void;
    /** Skill selected from the Skills panel — shown as a chip in the composer. */
    activeSkill?: string | null;
    onDismissSkill?: () => void;
}

export default function AgentPanel({
    turns,
    isBusy,
    onSubmitTask,
    onReviewDiffs,
    onFileDecision,
    onDismissError,
    onNewConversation,
    onSettingsClick,
    onCollapseClick,
    activeSkill,
    onDismissSkill,
}: AgentPanelProps) {
    return (
        <div className="hcode-agentchat">
            <AgentHeader
                onNewConversation={onNewConversation}
                onSettingsClick={onSettingsClick}
                onCollapseClick={onCollapseClick}
            />
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
