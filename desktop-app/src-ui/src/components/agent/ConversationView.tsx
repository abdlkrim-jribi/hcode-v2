/**
 * ConversationView — the scrolling transcript of turn cards.
 * Auto-scrolls to the newest activity; shows a calm empty state at rest.
 */
import React, { useEffect, useRef } from 'react';
import type { Turn } from '../../types';
import TurnCard from './TurnCard';

interface Props {
    turns: Turn[];
    onReviewDiffs: (turnId: string) => void;
    onFileDecision: (turnId: string, path: string, accepted: boolean) => void;
    onDismissError: (turnId: string) => void;
}

export default function ConversationView({ turns, onReviewDiffs, onFileDecision, onDismissError }: Props) {
    const endRef = useRef<HTMLDivElement>(null);

    // Follow the stream as new turns/tokens arrive. 'auto' (not 'smooth') keeps
    // token-by-token updates from animating jankily.
    useEffect(() => {
        endRef.current?.scrollIntoView({ block: 'end' });
    }, [turns]);

    if (turns.length === 0) {
        return (
            <div className="hcode-conv hcode-conv--empty">
                <div className="hcode-conv__empty">
                    <div className="hcode-conv__emptytitle">Start a conversation</div>
                    <div className="hcode-conv__emptyhint">
                        Describe a coding task below. The agent will plan, execute, and verify —
                        each turn stays in the transcript with its plan, diffs, and result.
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="hcode-conv">
            {turns.map(turn => (
                <TurnCard
                    key={turn.id}
                    turn={turn}
                    onReviewDiffs={onReviewDiffs}
                    onFileDecision={onFileDecision}
                    onDismissError={onDismissError}
                />
            ))}
            <div ref={endRef} />
        </div>
    );
}
