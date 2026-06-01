/**
 * Mock fixtures for the Skills browser panel.
 * Mirrors what `.hcode/skills/<name>/SKILL.md` front-matter contains.
 */
import type { SkillInfo } from '../components/SkillsPanel';

export const MOCK_SKILLS: SkillInfo[] = [
    {
        name: 'mcp-builder',
        description: 'Guide for building MCP clients and servers using the official Python MCP SDK.',
        category: 'integration',
        lastUsed: '2026-05-18',
    },
    {
        name: 'systematic-debugging',
        description: '4-phase root-cause-first debugging methodology.',
        category: 'debugging',
        lastUsed: '2026-05-22',
    },
    {
        name: 'verification-before-completion',
        description: 'Checklist for verifying work before marking done.',
        category: 'quality',
        lastUsed: '2026-05-26',
    },
    {
        name: 'clean-code',
        description: 'Apply clean code principles to any codebase.',
        category: 'coding',
        lastUsed: null,
    },
    {
        name: 'tdd-workflow',
        description: 'Test-Driven Development cycle (Red → Green → Refactor).',
        category: 'testing',
        lastUsed: null,
    },
];
