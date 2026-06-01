/**
 * Mock Event Stream — simulates a full PEV lifecycle for browser-mode testing.
 * When running outside Tauri (no daemon), this provides realistic events
 * so the UI can be demonstrated and tested standalone.
 * 
 * IMPORTANT: All content is generated dynamically from the user's task text.
 * Each run gets a unique runId so patch paths never collide between tasks.
 */
import type { AgentEvent } from '../types/agent-events';

let mockRunCounter = 0;

/** Creates a realistic sequence of agent events for demo purposes */
export function createMockEventStream(task: string): AgentEvent[] {
  const now = Date.now();
  const runId = ++mockRunCounter;

  // Generate a unique file path per run so patch identity never collides
  const filePath = `src/task_${runId}_output.py`;

  // Generate task-specific content
  const planMarkdown = `## Plan for: "${task}"\n\n1. Analyze the codebase for relevant files\n2. Apply requested changes\n3. Run tests to verify\n\n**Estimated changes:** 1 file`;
  const taskMd = `- [ ] ${task}`;
  const implPlanMd = `### Implementation\nExecute the following task: ${task}`;

  return [
    { type: 'agent_ready', timestamp: now },
    { type: 'task_submitted', task, mode: 'plan' as const, autonomous: false },

    // Planning phase
    { type: 'planning_started', timestamp: now + 500 },
    { type: 'streaming_chunk', content: `Analyzing: "${task}"...`, phase: 'planning' },
    { type: 'streaming_chunk', content: '\n\nIdentifying files to modify...', phase: 'planning' },
    {
      type: 'plan_created',
      markdown: planMarkdown,
      taskMd,
      implementationPlanMd: implPlanMd,
    },
    { type: 'plan_approved', timestamp: now + 4000 },

    // Execution phase
    { type: 'execution_started', timestamp: now + 4500 },
    { type: 'streaming_chunk', content: `Executing: ${task}...`, phase: 'executing' },
    {
      type: 'file_patch_proposed',
      path: filePath,
      diff: `@@ -1,3 +1,6 @@\n+# Modified by Hcode Agent (run ${runId})\n+# Task: ${task}\n+\n def main():\n     pass`,
      originalContent: 'def main():\n    pass\n',
      newContent: `# Modified by Hcode Agent (run ${runId})\n# Task: ${task}\n\ndef main():\n    """${task}"""\n    pass\n`,
      timestamp: now + 6000,
    },

    // Verification phase
    { type: 'verification_started', timestamp: now + 8000 },
    { type: 'streaming_chunk', content: 'Running tests...', phase: 'verifying' },
    {
      type: 'verification_completed',
      passed: true,
      markdown: `## Verification\n\nTask "${task}" completed successfully.\nAll tests passed. No regressions detected.`,
      testsRun: 5,
      testsPassed: 5,
      testsFailed: 0,
      timestamp: now + 10000,
    },

    // Done
    { type: 'done', summary: `Completed: ${task}`, timestamp: now + 10500 },
  ];
}

/**
 * Play a mock event stream with realistic delays.
 * Returns a cancel function.
 */
export function playMockStream(
  events: AgentEvent[],
  onEvent: (event: AgentEvent) => void,
  baseDelayMs = 800
): () => void {
  const timers: ReturnType<typeof setTimeout>[] = [];

  events.forEach((event, i) => {
    const timer = setTimeout(() => {
      onEvent(event);
    }, i * baseDelayMs);
    timers.push(timer);
  });

  // Return cancel function
  return () => {
    timers.forEach(clearTimeout);
  };
}

/**
 * Map AgentEvent back to HcodeMessage shape for the existing onDaemonMessage handler.
 * This bridges the new event model to the existing App.tsx message switch.
 */
export function agentEventToHcodeMessage(event: AgentEvent): Record<string, unknown> | null {
  switch (event.type) {
    case 'agent_ready':
      return { type: 'ready' };
    case 'planning_started':
      return { type: 'agent_phase', phase: 'planning' };
    case 'plan_created':
      return { type: 'plan', payload: { markdown: event.markdown } };
    case 'execution_started':
      return { type: 'agent_phase', phase: 'executing' };
    case 'task_update':
      return { type: 'task_update', payload: { markdown: event.markdown } };
    case 'streaming_chunk':
      // Map streaming_chunk to task_update so the onDaemonMessage handler processes it
      return { type: 'task_update', payload: { markdown: event.content } };
    case 'file_patch_proposed':
      return {
        type: 'file_patch',
        payload: {
          path: event.path,
          diff: event.diff,
          backup: '',
          originalContent: event.originalContent,
          newContent: event.newContent,
        },
      };
    case 'verification_started':
      return { type: 'agent_phase', phase: 'verifying' };
    case 'verification_completed':
      return {
        type: 'verification',
        payload: {
          markdown: event.markdown,
          passed: event.passed,
          testResults: event.testsRun !== undefined
            ? `${event.testsPassed}/${event.testsRun} passed`
            : undefined,
        },
      };
    case 'error':
      return { type: 'error', payload: { message: event.message, suggestion: '' } };
    case 'circuit_break':
      return { type: 'circuit_break', payload: { reason: event.reason } };
    case 'done':
      return { type: 'done' };
    default:
      return null;
  }
}
