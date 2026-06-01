/**
 * VerificationPanel — displays test/verification results.
 * Ported from VS Code extension.
 */
import React from 'react';
import type { VerificationPayload } from '../types';

interface Props {
    result: VerificationPayload | null;
}

export default function VerificationPanel({ result }: Props) {
    if (!result) {
        return (
            <div style={{ textAlign: 'center', color: 'var(--hcode-fg-dim)', padding: '32px 16px' }}>
                <div style={{ fontSize: '32px', marginBottom: '8px' }}>✅</div>
                <div>No verification results yet.</div>
            </div>
        );
    }

    return (
        <div className="hcode-card">
            <h2 className="hcode-card-title">
                {result.passed ? '✅ Verification Passed' : '❌ Verification Failed'}
            </h2>

            <div style={{
                fontFamily: 'var(--font-sans)',
                fontSize: 'var(--font-size-sm)',
                whiteSpace: 'pre-wrap',
                lineHeight: '1.6',
            }}>
                {result.markdown}
            </div>

            {result.testResults && (
                <div style={{ marginTop: 'var(--sp-3)' }}>
                    <h3 style={{ fontSize: 'var(--font-size-sm)', marginBottom: 'var(--sp-2)' }}>Test Output</h3>
                    <div className="hcode-logs">
                        <pre style={{ margin: 0 }}>{result.testResults}</pre>
                    </div>
                </div>
            )}
        </div>
    );
}
