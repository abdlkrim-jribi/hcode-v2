/**
 * StreamingDisplay — Shows agent thinking/streaming content in real-time.
 * Auto-scrolls to bottom unless the user has manually scrolled up.
 * Phase 2 / Checkpoint 2: Task 5 (Streaming UX).
 */
import React, { useEffect, useRef } from 'react';

interface StreamingDisplayProps {
    content: string;
    phase: string;
}

export default function StreamingDisplay({ content, phase }: StreamingDisplayProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const userScrolledRef = useRef(false);

    // Detect manual scroll-up
    const handleScroll = () => {
        if (!containerRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
        const isNearBottom = scrollHeight - scrollTop - clientHeight < 20;
        userScrolledRef.current = !isNearBottom;
    };

    // Auto-scroll to bottom (unless user scrolled up)
    useEffect(() => {
        if (!userScrolledRef.current && containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
    }, [content]);

    // Reset scroll lock when phase changes
    useEffect(() => {
        userScrolledRef.current = false;
    }, [phase]);

    if (!content) return null;

    const phaseLabel = phase.charAt(0).toUpperCase() + phase.slice(1);

    return (
        <div className="hcode-streaming">
            <div className="hcode-streaming__header">
                <span className={`hcode-phase-dot is-${phase}`} />
                <span className="hcode-streaming__phase">{phaseLabel}</span>
                <span className="hcode-streaming__indicator">●●●</span>
            </div>
            <div
                className="hcode-streaming__content"
                ref={containerRef}
                onScroll={handleScroll}
            >
                <pre className="hcode-streaming__text">{content}</pre>
            </div>
        </div>
    );
}
