/**
 * DiffReviewer — Monaco-based diff viewer with per-file accept/reject.
 * Phase 2: Added loading states, error handling, and visual feedback.
 */
import React, { useEffect, useRef, useState } from 'react';
import * as monaco from 'monaco-editor';
import type { FilePatchPayload } from '../types';

interface Props {
    patches: FilePatchPayload[];
    appliedPatches?: string[];
    onFileDecision: (path: string, accepted: boolean) => void;
    onRollback: () => void;
    lastError?: string | null;
}

export default function DiffReviewer({ patches, appliedPatches = [], onFileDecision, onRollback, lastError }: Props) {
    const [selected, setSelected] = useState<number>(0);
    const [decisions, setDecisions] = useState<Record<string, boolean | null>>({});
    const [loadingPaths, setLoadingPaths] = useState<Set<string>>(new Set());
    const editorRef = useRef<HTMLDivElement>(null);
    const diffEditorRef = useRef<monaco.editor.IStandaloneDiffEditor | null>(null);

    // Reset local state when patches change (new task submitted)
    const prevPatchesRef = useRef(patches);
    useEffect(() => {
        if (prevPatchesRef.current !== patches) {
            setSelected(0);
            setDecisions({});
            setLoadingPaths(new Set());
            prevPatchesRef.current = patches;
        }
    }, [patches]);

    const patch = patches[selected];

    useEffect(() => {
        if (!editorRef.current || !patch) return;

        if (diffEditorRef.current) {
            diffEditorRef.current.dispose();
        }

        diffEditorRef.current = monaco.editor.createDiffEditor(editorRef.current, {
            readOnly: true,
            theme: 'vs-dark',
            renderSideBySide: true,
            automaticLayout: true,
            minimap: { enabled: false },
            scrollbar: {
                vertical: 'visible',
                horizontal: 'visible',
            }
        });

        const originalModel = monaco.editor.createModel(
            patch.originalContent || '',
            undefined,
            monaco.Uri.parse(`original://${patch.path}`)
        );
        const modifiedModel = monaco.editor.createModel(
            patch.newContent || '',
            undefined,
            monaco.Uri.parse(`modified://${patch.path}`)
        );

        diffEditorRef.current.setModel({ original: originalModel, modified: modifiedModel });

        return () => {
            originalModel.dispose();
            modifiedModel.dispose();
        };
    }, [patch]);

    if (patches.length === 0) {
        return (
            <div className="hcode-editor-empty">
                <div className="hcode-editor-empty__text">
                    {appliedPatches.length > 0
                        ? `${appliedPatches.length} file${appliedPatches.length > 1 ? 's' : ''} applied`
                        : 'No Changes'}
                </div>
                <div className="hcode-editor-empty__hint">
                    {appliedPatches.length > 0
                        ? 'All patches have been reviewed and applied.'
                        : 'All file changes have been reviewed.'}
                </div>
            </div>
        );
    }

    const decide = async (path: string, accepted: boolean) => {
        setLoadingPaths(prev => new Set(prev).add(path));
        setDecisions(prev => ({ ...prev, [path]: accepted }));
        onFileDecision(path, accepted);
        // Loading will clear when patch is removed from state by the reducer
        // (via ACCEPT_PATCH or REJECT_PATCH action)
        setTimeout(() => {
            setLoadingPaths(prev => {
                const next = new Set(prev);
                next.delete(path);
                return next;
            });
        }, 2000);
    };

    const allDecided = patches.every((p) => decisions[p.path] !== undefined);
    const isLoading = patch && loadingPaths.has(patch.path);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            {/* Header / Tabs */}
            <div className="hcode-panel-header" style={{ display: 'flex', gap: 'var(--space-2)', overflowX: 'auto' }}>
                <span style={{ fontSize: 'var(--text-xs)', fontWeight: 600, color: 'var(--fg-secondary)', marginRight: 'var(--space-2)' }}>REVIEW</span>
                {patches.map((p, i) => {
                    const d = decisions[p.path];
                    const loading = loadingPaths.has(p.path);
                    const baseName = p.path.split(/[\\/]/).pop();
                    const statusDot = loading
                        ? <span className="hcode-phase-dot is-thinking" style={{ marginRight: 'var(--space-2)' }} />
                        : d === true
                            ? <span className="hcode-phase-dot is-done" style={{ marginRight: 'var(--space-2)' }} />
                            : d === false
                                ? <span className="hcode-phase-dot is-error" style={{ marginRight: 'var(--space-2)' }} />
                                : <span className="hcode-phase-dot is-verifying" style={{ marginRight: 'var(--space-2)' }} />;

                    return (
                        <button
                            key={p.path}
                            onClick={() => setSelected(i)}
                            style={{
                                background: i === selected ? 'var(--surface-3)' : 'transparent',
                                border: '1px solid',
                                borderColor: i === selected ? 'var(--border-strong)' : 'transparent',
                                color: i === selected ? 'var(--fg-primary)' : 'var(--fg-secondary)',
                                padding: 'var(--space-1) var(--space-3)',
                                borderRadius: 'var(--radius-sm)',
                                fontSize: 'var(--text-xs)',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                opacity: loading ? 0.6 : 1,
                            }}
                        >
                            {statusDot}
                            {baseName}
                        </button>
                    );
                })}

                {/* Applied patches summary */}
                {appliedPatches.length > 0 && (
                    <span style={{
                        fontSize: 'var(--text-2xs)',
                        color: 'var(--semantic-success)',
                        alignSelf: 'center',
                        marginLeft: 'auto',
                    }}>
                        {appliedPatches.length} applied
                    </span>
                )}
            </div>

            {/* Error Banner */}
            {lastError && (
                <div style={{
                    padding: 'var(--space-2) var(--space-3)',
                    background: 'var(--surface-error, rgba(255,80,80,0.1))',
                    borderBottom: '1px solid var(--semantic-error)',
                    fontSize: 'var(--text-xs)',
                    color: 'var(--semantic-error)',
                }}>
                    ⚠ {lastError}
                </div>
            )}

            {/* Monaco Diff Container */}
            <div className="hcode-editor-body" style={{ flex: 1, position: 'relative' }}>
                <div ref={editorRef} style={{ position: 'absolute', inset: 0 }} />
            </div>

            {/* Bottom Action Bar */}
            <div style={{
                padding: 'var(--space-3)',
                background: 'var(--surface-1)',
                borderTop: '1px solid var(--border-default)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between'
            }}>
                <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                    {patch && decisions[patch.path] === undefined ? (
                        <>
                            <button
                                className="hcode-btn hcode-btn--primary"
                                onClick={() => decide(patch.path, true)}
                                disabled={isLoading}
                            >
                                {isLoading ? 'Accepting...' : 'Accept File'}
                            </button>
                            <button
                                className="hcode-btn hcode-btn--danger"
                                onClick={() => decide(patch.path, false)}
                                disabled={isLoading}
                            >
                                {isLoading ? 'Rejecting...' : 'Reject File'}
                            </button>
                        </>
                    ) : (
                        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--fg-secondary)' }}>
                            File {decisions[patch?.path || ''] ? '● Accepted' : '✕ Rejected'}
                        </span>
                    )}
                </div>

                <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
                    {allDecided && <span style={{ fontSize: 'var(--text-xs)', color: 'var(--semantic-success)' }}>All files reviewed</span>}
                    <button className="hcode-btn hcode-btn--ghost" onClick={onRollback}>
                        Close & Rollback All
                    </button>
                </div>
            </div>
        </div>
    );
}
