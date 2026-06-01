/**
 * WorkflowsPanel — lists workflows from the v2 daemon (list_workflows) and
 * runs them via run_workflow. VITE_MOCK returns fixtures through bridge.ts.
 */
import { useEffect, useState } from 'react';
import * as ipc from '../ipc/bridge';

export type WorkflowStatus = 'idle' | 'running' | 'done' | 'error';

export interface WorkflowInfo {
    name: string;
    description: string;
    stepCount: number;
    lastRun: string | null;
    status: WorkflowStatus;
    errorMessage?: string;
}

const statusColor: Record<WorkflowStatus, string> = {
    idle:    'var(--fg-secondary, #888)',
    running: 'var(--accent, #3b82f6)',
    done:    'var(--semantic-success, #22c55e)',
    error:   'var(--semantic-error, #ef4444)',
};

const statusLabel: Record<WorkflowStatus, string> = {
    idle: 'Idle', running: 'Running…', done: 'Done', error: 'Error',
};

export default function WorkflowsPanel() {
    const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [busyName, setBusyName] = useState<string | null>(null);

    useEffect(() => {
        ipc.listWorkflows()
            .then(ws => setWorkflows(ws.map(w => ({ ...w, status: (w.status as WorkflowStatus) ?? 'idle' }))))
            .catch(() => setWorkflows([]))
            .finally(() => setLoading(false));
    }, []);

    const handleRun = async (wf: WorkflowInfo) => {
        if (wf.status === 'running' || busyName) return;
        setBusyName(wf.name);
        setWorkflows(prev => prev.map(w => w.name === wf.name ? { ...w, status: 'running' } : w));
        try {
            await ipc.runWorkflow(wf.name);
            setWorkflows(prev => prev.map(w => w.name === wf.name ? { ...w, status: 'done', lastRun: new Date().toISOString() } : w));
        } catch (err) {
            setWorkflows(prev => prev.map(w => w.name === wf.name ? { ...w, status: 'error', errorMessage: String(err) } : w));
        } finally {
            setBusyName(null);
        }
    };

    return (
        <div className="hcode-workflows-panel" style={{ padding: 'var(--space-3)' }}>
            <header style={{ marginBottom: 'var(--space-3)' }}>
                <h3 style={{ margin: 0, fontSize: 'var(--text-sm)' }}>Workflows</h3>
                <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)' }}>
                    Loaded from <code>.hcode/workflows/</code>. Click <kbd>Run</kbd> to execute.
                </p>
            </header>

            {loading && <div style={{ color: 'var(--fg-secondary)', fontSize: 'var(--text-xs)' }}>Loading…</div>}

            {!loading && workflows.length === 0 && (
                <div style={{ color: 'var(--fg-secondary)', fontSize: 'var(--text-xs)' }}>
                    No workflows. Drop a <code>.md</code> file into <code>.hcode/workflows/</code>.
                </div>
            )}

            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {workflows.map(wf => (
                    <li key={wf.name} style={{
                        padding: 'var(--space-2)', marginBottom: 'var(--space-1)',
                        border: '1px solid var(--border-default)', borderRadius: 4,
                        background: 'var(--surface-2)',
                    }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <span style={{ fontWeight: 500, fontSize: 'var(--text-xs)' }}>{wf.name}</span>
                            <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
                                <span style={{ fontSize: 'var(--text-xs)', color: statusColor[wf.status] }}>
                                    {statusLabel[wf.status]}
                                </span>
                                <button
                                    className="hcode-btn hcode-btn--primary"
                                    style={{ padding: '2px 8px', fontSize: 'var(--text-xs)' }}
                                    disabled={wf.status === 'running' || busyName !== null}
                                    onClick={() => handleRun(wf)}
                                >
                                    Run
                                </button>
                            </div>
                        </div>
                        {wf.description && (
                            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', marginTop: 2 }}>
                                {wf.description}
                            </div>
                        )}
                        {wf.errorMessage && (
                            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--semantic-error, #ef4444)', marginTop: 2 }}>
                                {wf.errorMessage}
                            </div>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}
