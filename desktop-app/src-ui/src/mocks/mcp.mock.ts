/**
 * Mock fixtures for the MCP Connections panel.
 * Used when VITE_MOCK=true (Sprint A gate) or by the inline storybook view.
 */
import type { MCPServerInfo, MCPStatus } from '../components/MCPPanel';

export const MOCK_MCP_SERVERS: MCPServerInfo[] = [
    {
        id: 'filesystem',
        name: 'Filesystem',
        description: 'Access and manipulate local files',
        status: 'connected',
        toolCount: 7,
    },
    {
        id: 'github',
        name: 'GitHub',
        description: 'GitHub integration via MCP',
        status: 'disconnected',
        toolCount: 0,
    },
    {
        id: 'sqlite',
        name: 'SQLite (local.db)',
        description: 'SQLite database access',
        status: 'error',
        toolCount: 0,
        errorMessage: 'Subprocess exited with code 1 — check uvx installation',
    },
];

export type { MCPStatus };
