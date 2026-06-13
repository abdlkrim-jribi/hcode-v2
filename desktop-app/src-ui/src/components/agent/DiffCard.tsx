/**
 * DiffCard — compact, per-turn diff preview shown INSIDE a turn card.
 *
 * Renders a colored unified-diff preview for each proposed file with inline
 * Accept / Reject, plus an "Open in editor" affordance that hands the turn's
 * patches to the full Monaco DiffReviewer in the center panel.
 */
import React from 'react';
import type { FilePatchPayload } from '../../types';

interface Props {
    turnId: string;
    patches: FilePatchPayload[];
    appliedPatches: string[];
    rejectedPatches: string[];
    onFileDecision: (turnId: string, path: string, accepted: boolean) => void;
    onReviewDiffs: (turnId: string) => void;
}

const PREVIEW_LINES = 16;

function baseName(path: string): string {
    return path.split(/[\\/]/).pop() ?? path;
}

function diffLineClass(line: string): string {
    if (line.startsWith('+') && !line.startsWith('+++')) return 'added';
    if (line.startsWith('-') && !line.startsWith('---')) return 'removed';
    if (line.startsWith('@@')) return 'hunk';
    return '';
}

export default function DiffCard({
    turnId, patches, appliedPatches, rejectedPatches, onFileDecision, onReviewDiffs,
}: Props) {
    const decidedCount = appliedPatches.length + rejectedPatches.length;
    if (patches.length === 0 && decidedCount === 0) return null;

    return (
        <div className="hcode-card hcode-diffcard">
            <div className="hcode-card__title">
                <span>Changes</span>
                <span className="hcode-diffcard__meta">
                    {patches.length > 0 && <span>{patches.length} pending</span>}
                    {appliedPatches.length > 0 && <span className="is-applied">{appliedPatches.length} applied</span>}
                    {rejectedPatches.length > 0 && <span className="is-rejected">{rejectedPatches.length} rejected</span>}
                </span>
            </div>

            <div className="hcode-card__body">
                {patches.map(patch => {
                    const lines = patch.diff.split('\n');
                    const shown = lines.slice(0, PREVIEW_LINES);
                    const hidden = lines.length - shown.length;
                    return (
                        <div key={patch.path} className="hcode-diffcard__file">
                            <div className="hcode-diffcard__filehead">
                                <span className="hcode-diffcard__filename" title={patch.path}>{baseName(patch.path)}</span>
                                <div className="hcode-diffcard__fileactions">
                                    <button
                                        className="hcode-btn hcode-btn--primary hcode-btn--small"
                                        onClick={() => onFileDecision(turnId, patch.path, true)}
                                    >
                                        Accept
                                    </button>
                                    <button
                                        className="hcode-btn hcode-btn--ghost hcode-btn--small"
                                        onClick={() => onFileDecision(turnId, patch.path, false)}
                                    >
                                        Reject
                                    </button>
                                </div>
                            </div>
                            <pre className="hcode-diffcard__diff">
                                {shown.map((line, i) => (
                                    <div key={i} className={`hcode-diffline ${diffLineClass(line)}`}>{line || ' '}</div>
                                ))}
                                {hidden > 0 && <div className="hcode-diffline hcode-diffline--more">… {hidden} more line{hidden > 1 ? 's' : ''}</div>}
                            </pre>
                        </div>
                    );
                })}

                {appliedPatches.map(p => (
                    <div key={p} className="hcode-diffcard__decided is-applied">✓ {baseName(p)} accepted</div>
                ))}
                {rejectedPatches.map(p => (
                    <div key={p} className="hcode-diffcard__decided is-rejected">✕ {baseName(p)} rejected</div>
                ))}
            </div>

            {patches.length > 0 && (
                <div className="hcode-card__actions">
                    <button className="hcode-btn hcode-btn--secondary hcode-btn--small" onClick={() => onReviewDiffs(turnId)}>
                        Open in editor
                    </button>
                </div>
            )}
        </div>
    );
}
