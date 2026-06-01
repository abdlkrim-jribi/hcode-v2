/**
 * StatusBar — bottom bar showing daemon status, current phase, pending patches, and working directory.
 * Phase 2 / Checkpoint 2: Enhanced with PhaseIndicator and DaemonIndicator sub-components.
 */
import React from 'react';
import type { AgentPhase, DaemonStatus } from '../types';

interface Props {
    daemonStatus: DaemonStatus;
    phase: AgentPhase;
    workDir: string;
    pendingPatchCount?: number;
    circuitBreakActive?: boolean;
    currentTask?: string | null;
}

// ── Phase Indicator ──────────────────────────────────────────────────────────

const PHASE_CONFIG: Record<string, { color: string; label: string; icon: string }> = {
    idle:      { color: 'var(--fg-tertiary)',       label: 'Idle',      icon: '○' },
    thinking:  { color: 'var(--phase-thinking)',     label: 'Thinking',  icon: '◉' },
    planning:  { color: 'var(--phase-planning)',     label: 'Planning',  icon: '◉' },
    executing: { color: 'var(--phase-executing)',    label: 'Executing', icon: '◉' },
    verifying: { color: 'var(--phase-verifying)',    label: 'Verifying', icon: '◉' },
    done:      { color: 'var(--semantic-success)',   label: 'Done',      icon: '✓' },
    error:     { color: 'var(--semantic-error)',     label: 'Error',     icon: '✗' },
};

function PhaseIndicator({ phase }: { phase: AgentPhase }) {
    const config = PHASE_CONFIG[phase] || PHASE_CONFIG.idle;
    const isActive = !['idle', 'done', 'error'].includes(phase);

    return (
        <span
            className={`hcode-statusbar__phase ${isActive ? 'is-active' : ''}`}
            style={{ color: config.color }}
            title={`Agent phase: ${config.label}`}
        >
            <span className={isActive ? 'hcode-status-pulse' : ''}>{config.icon}</span>
            {' '}{config.label}
        </span>
    );
}

// ── Daemon Indicator ─────────────────────────────────────────────────────────

const DAEMON_CONFIG: Record<string, { color: string; label: string; icon: string }> = {
    running:    { color: 'var(--semantic-success)', label: 'Connected',    icon: '●' },
    starting:   { color: 'var(--semantic-warning)', label: 'Starting',     icon: '◐' },
    restarting: { color: 'var(--semantic-warning)', label: 'Restarting',   icon: '◐' },
    error:      { color: 'var(--semantic-error)',   label: 'Error',        icon: '○' },
    stopped:    { color: 'var(--fg-tertiary)',      label: 'Disconnected', icon: '○' },
};

function DaemonIndicator({ status }: { status: DaemonStatus }) {
    const config = DAEMON_CONFIG[status] || DAEMON_CONFIG.stopped;
    return (
        <span
            className="hcode-statusbar__daemon"
            style={{ color: config.color }}
            title={`Daemon: ${config.label}`}
        >
            {config.icon} Daemon
        </span>
    );
}

// ── StatusBar ────────────────────────────────────────────────────────────────

export default function StatusBar({ daemonStatus, phase, workDir, pendingPatchCount = 0, circuitBreakActive = false, currentTask }: Props) {
    return (
        <div className="hcode-statusbar">
            {/* Left: Phase + current task */}
            <div className="hcode-statusbar__left">
                <PhaseIndicator phase={phase} />

                {currentTask && phase !== 'idle' && phase !== 'done' && (
                    <span
                        className="hcode-statusbar__task"
                        title={currentTask}
                    >
                        {currentTask.length > 40 ? currentTask.substring(0, 37) + '...' : currentTask}
                    </span>
                )}
            </div>

            {/* Center: Pending patches */}
            <div className="hcode-statusbar__center">
                {pendingPatchCount > 0 && (
                    <span className="hcode-statusbar__pending" title={`${pendingPatchCount} files pending review`}>
                        <span className="hcode-phase-dot is-executing" />
                        {pendingPatchCount} pending patch{pendingPatchCount > 1 ? 'es' : ''}
                    </span>
                )}
            </div>

            {/* Right: Circuit break + daemon + workdir */}
            <div className="hcode-statusbar__right">
                {circuitBreakActive && (
                    <span
                        className="hcode-statusbar__circuit-break"
                        title="Circuit breaker active — agent paused due to repeated errors"
                    >
                        ⚠ Circuit Break
                    </span>
                )}
                <DaemonIndicator status={daemonStatus} />
                {workDir && (
                    <span className="hcode-statusbar__workdir" title={workDir}>
                        ◉ {workDir.split(/[\\/]/).pop()}
                    </span>
                )}
            </div>
        </div>
    );
}
