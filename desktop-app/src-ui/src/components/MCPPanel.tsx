/**
 * MCPPanel — lists available MCP servers from the v2 daemon (list_mcp_servers)
 * and connects/disconnects via connect_mcp_server / disconnect_mcp_server.
 * VITE_MOCK mode returns mock fixtures through bridge.ts.
 */
import { useEffect, useState } from 'react';
import * as ipc from '../ipc/bridge';

export type MCPStatus = 'connected' | 'disconnected' | 'connecting' | 'error' | 'needs-auth';

export interface MCPServerInfo {
    id: string;
    name: string;
    description: string;
    status: MCPStatus;
    toolCount: number;
    configured?: boolean;
    errorMessage?: string;
    tools?: string[];
}

const statusColor: Record<MCPStatus, string> = {
    connected:    'var(--semantic-success, #22c55e)',
    disconnected: 'var(--fg-secondary, #888)',
    connecting:   'var(--accent, #3b82f6)',
    error:        'var(--semantic-error, #ef4444)',
    'needs-auth': 'var(--semantic-warning, #f59e0b)',
};

// Label differs by state. A not-connected server reads "Configured" when it's in
// the config file, else "Available". needs-auth points the user at Phase 2.
function statusLabel(s: MCPServerInfo): string {
    switch (s.status) {
        case 'connected':   return s.toolCount > 0 ? `Connected · ${s.toolCount} tools` : 'Connected';
        case 'connecting':  return 'Connecting…';
        case 'error':       return 'Error';
        case 'needs-auth':  return 'Needs auth (Phase 2)';
        default:            return s.configured ? 'Configured' : 'Available';
    }
}

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
        // Phase 1: token servers can't be connected from the UI yet (Phase 2).
        if (server.status === 'needs-auth') return;
        setBusyId(server.id);
        setServers(prev => prev.map(s => s.id === server.id ? { ...s, status: 'connecting' } : s));
        try {
            if (server.status === 'connected') {
                await ipc.disconnectMcpServer(server.name);
                setServers(prev => prev.map(s => s.id === server.id
                    ? { ...s, status: 'disconnected', toolCount: 0, configured: false, tools: undefined } : s));
            } else {
                // The daemon returns the REAL outcome: 'connected' with a live tool
                // count, or 'needs-auth' (don't claim connected). A real failure throws.
                const res = await ipc.connectMcpServer(server.name);
                setServers(prev => prev.map(s => s.id === server.id ? {
                    ...s,
                    status: (res.status as MCPStatus) ?? 'connected',
                    toolCount: res.toolCount ?? 0,
                    tools: res.tools,
                    configured: res.status === 'connected',
                    errorMessage: res.status === 'needs-auth' ? res.message : undefined,
                } : s));
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
                                    {statusLabel(server)}
                                </span>
                                <button
                                    className="hcode-btn hcode-btn--primary"
                                    style={{ padding: '2px 8px', fontSize: 'var(--text-xs)' }}
                                    disabled={
                                        server.status === 'connecting'
                                        || server.status === 'needs-auth'
                                        || busyId !== null
                                    }
                                    title={server.status === 'needs-auth'
                                        ? 'Secure token entry lands in Phase 2'
                                        : undefined}
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
                        {server.errorMessage && (
                            <div style={{
                                fontSize: 'var(--text-xs)',
                                color: statusColor[server.status === 'error' ? 'error' : 'needs-auth'],
                                marginTop: 2,
                            }}>
                                {server.errorMessage}
                            </div>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}
