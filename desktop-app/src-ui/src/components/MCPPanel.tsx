/**
 * MCPPanel — lists available MCP servers from the v2 daemon (list_mcp_servers)
 * and connects/disconnects via connect_mcp_server / disconnect_mcp_server.
 * VITE_MOCK mode returns mock fixtures through bridge.ts.
 */
import { useEffect, useState } from 'react';
import * as ipc from '../ipc/bridge';

export type MCPStatus = 'connected' | 'disconnected' | 'connecting' | 'error';

export interface MCPServerInfo {
    id: string;
    name: string;
    description: string;
    status: MCPStatus;
    toolCount: number;
    errorMessage?: string;
}

const statusColor: Record<MCPStatus, string> = {
    connected:    'var(--semantic-success, #22c55e)',
    disconnected: 'var(--fg-secondary, #888)',
    connecting:   'var(--accent, #3b82f6)',
    error:        'var(--semantic-error, #ef4444)',
};

const statusLabel: Record<MCPStatus, string> = {
    connected: 'Connected', disconnected: 'Disconnected', connecting: 'Connecting…', error: 'Error',
};

export default function MCPPanel() {
    const [servers, setServers] = useState<MCPServerInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [busyId, setBusyId] = useState<string | null>(null);

    useEffect(() => {
        ipc.listMcpServers()
            .then(s => setServers(s.map(sv => ({ ...sv, status: (sv.status as MCPStatus) ?? 'disconnected' }))))
            .catch(() => setServers([]))
            .finally(() => setLoading(false));
    }, []);

    const handleToggle = async (server: MCPServerInfo) => {
        if (busyId) return;
        setBusyId(server.id);
        setServers(prev => prev.map(s => s.id === server.id ? { ...s, status: 'connecting' } : s));
        try {
            if (server.status === 'connected') {
                await ipc.disconnectMcpServer(server.name);
                setServers(prev => prev.map(s => s.id === server.id ? { ...s, status: 'disconnected' } : s));
            } else {
                await ipc.connectMcpServer(server.name);
                setServers(prev => prev.map(s => s.id === server.id ? { ...s, status: 'connected' } : s));
            }
        } catch (err) {
            setServers(prev => prev.map(s => s.id === server.id ? { ...s, status: 'error', errorMessage: String(err) } : s));
        } finally {
            setBusyId(null);
        }
    };

    return (
        <div className="hcode-mcp-panel" style={{ padding: 'var(--space-3)' }}>
            <header style={{ marginBottom: 'var(--space-3)' }}>
                <h3 style={{ margin: 0, fontSize: 'var(--text-sm)' }}>MCP Servers</h3>
                <p style={{ margin: 'var(--space-1) 0 0', fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)' }}>
                    Connected servers expose tools as <code>mcp_&lt;server&gt;_&lt;tool&gt;</code>.
                </p>
            </header>

            {loading && <div style={{ color: 'var(--fg-secondary)', fontSize: 'var(--text-xs)' }}>Loading…</div>}

            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {servers.map(server => (
                    <li key={server.id} style={{
                        padding: 'var(--space-2)', marginBottom: 'var(--space-1)',
                        border: '1px solid var(--border-default)', borderRadius: 4,
                        background: 'var(--surface-2)',
                    }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <span style={{ fontWeight: 500, fontSize: 'var(--text-xs)' }}>{server.name}</span>
                            <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
                                <span style={{ fontSize: 'var(--text-xs)', color: statusColor[server.status] }}>
                                    {statusLabel[server.status]}
                                </span>
                                <button
                                    className="hcode-btn hcode-btn--primary"
                                    style={{ padding: '2px 8px', fontSize: 'var(--text-xs)' }}
                                    disabled={server.status === 'connecting' || busyId !== null}
                                    onClick={() => handleToggle(server)}
                                >
                                    {server.status === 'connected' ? 'Disconnect' : 'Connect'}
                                </button>
                            </div>
                        </div>
                        {server.description && (
                            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', marginTop: 2 }}>
                                {server.description}
                            </div>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}
