/**
 * AgentChat — phase-aware chat message thread.
 * Ported from VS Code extension.
 */
import React, { useEffect, useRef } from 'react';
import type { AgentPhase, ChatMessage } from '../types';
import { PHASE_CONFIGS } from '../types';

interface Props {
    messages: ChatMessage[];
    phase: AgentPhase;
}

export default function AgentChat({ messages, phase }: Props) {
    const endRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        endRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages.length]);

    return (
        <div className="hcode-chat">
            {messages.length === 0 && (
                <div style={{ textAlign: 'center', color: 'var(--hcode-fg-dim)', padding: '32px 16px' }}>
                    <div style={{ fontSize: '32px', marginBottom: '8px' }}>💬</div>
                    <div>No messages yet. Submit a task to get started.</div>
                </div>
            )}

            {messages.map((msg) => {
                let className = 'hcode-chat-msg';
                if (msg.type === 'user') className += ' hcode-chat-msg--user';
                else if (msg.type === 'system' || msg.type === 'agent_phase') className += ' hcode-chat-msg--system';
                else className += ' hcode-chat-msg--agent';

                return (
                    <div key={msg.id} className={className}>
                        {msg.type === 'agent_phase' && msg.phase && (
                            <span style={{ marginRight: '4px' }}>{PHASE_CONFIGS[msg.phase].emoji}</span>
                        )}
                        <span>{msg.content}</span>
                        <div className="hcode-chat-msg__time">{msg.timestamp}</div>
                    </div>
                );
            })}

            {phase === 'thinking' && (
                <div className="hcode-chat-msg hcode-chat-msg--agent" style={{ animation: 'pulse 1.5s infinite' }}>
                    🟣 Thinking...
                </div>
            )}

            <div ref={endRef} />
        </div>
    );
}
