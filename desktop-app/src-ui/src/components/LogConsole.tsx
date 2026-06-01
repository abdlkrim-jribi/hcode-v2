/**
 * LogConsole — monospace log viewer for daemon stdout/stderr.
 * Ported from VS Code extension.
 */
import React, { useEffect, useRef } from 'react';
import type { LogPayload } from '../types';

interface Props {
    logs: LogPayload[];
}

export default function LogConsole({ logs }: Props) {
    const endRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        endRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [logs.length]);

    if (logs.length === 0) {
        return (
            <div style={{ textAlign: 'center', color: 'var(--hcode-fg-dim)', padding: '32px 16px' }}>
                <div style={{ fontSize: '32px', marginBottom: '8px' }}>📜</div>
                <div>No logs yet.</div>
            </div>
        );
    }

    return (
        <div className="hcode-logs">
            {logs.map((log, i) => (
                <div
                    key={i}
                    className={`hcode-log-line ${log.stream === 'stderr' ? 'hcode-log-line--stderr' : ''}`}
                >
                    {log.line}
                </div>
            ))}
            <div ref={endRef} />
        </div>
    );
}
