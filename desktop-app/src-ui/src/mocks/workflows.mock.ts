/**
 * Mock fixtures for the Workflows panel.
 * Mirrors `.hcode/workflows/<name>.md` front-matter.
 */
import type { WorkflowInfo } from '../components/WorkflowsPanel';

export const MOCK_WORKFLOWS: WorkflowInfo[] = [
    {
        name: 'deploy',
        description: 'Build and deploy the application to staging',
        stepCount: 4,
        lastRun: '2026-05-18',
        status: 'idle',
    },
    {
        name: 'release',
        description: 'Tag, build, publish to PyPI',
        stepCount: 7,
        lastRun: '2026-05-10',
        status: 'idle',
    },
    {
        name: 'nightly-tests',
        description: 'Full test matrix on Windows/macOS/Linux',
        stepCount: 3,
        lastRun: null,
        status: 'idle',
    },
];
