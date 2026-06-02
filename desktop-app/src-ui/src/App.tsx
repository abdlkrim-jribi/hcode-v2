/**
 * App.tsx — HCode v2 desktop shell.
 *
 * v2 additions vs v1:
 *  - onDaemonMessage handles C2 streaming events:
 *      streaming_chunk, planning_started, execution_started,
 *      verification_started, plan_created  (all emitted by bridge.py)
 *  - SkillsPanel / WorkflowsPanel / MCPPanel now call ipc bridge methods
 *    (list_skills, list_workflows, run_workflow, list_mcp_servers,
 *     connect/disconnect_mcp_server) — mocked by default (VITE_MOCK=true).
 */
import React, { useReducer, useEffect, useCallback, useRef, useState, useMemo } from 'react';
import type { AppState, AgentPhase, HcodeMessage, FileEntry, FilePatchPayload, LogPayload, ChatMessage } from './types';
import type { AgentEvent } from './types/agent-events';
import { INITIAL_STATE } from './types';

import AgentPanel from './components/AgentPanel';
import StatusBar from './components/StatusBar';
import MonacoEditor from './components/MonacoEditor';
import FileExplorer from './components/FileExplorer';
import DiffReviewer from './components/DiffReviewer';
import SettingsPanel from './components/SettingsPanel';
import CommandPalette from './components/CommandPalette';
import type { Command } from './components/CommandPalette';
import WelcomeScreen from './components/WelcomeScreen';
import MCPPanel from './components/MCPPanel';
import SkillsPanel from './components/SkillsPanel';
import WorkflowsPanel from './components/WorkflowsPanel';

import * as ipc from './ipc/bridge';

// ── Actions ───────────────────────────────────────────────────────────────────

type Action =
  | { type: 'SET_PHASE'; phase: AgentPhase }
  | { type: 'SET_DAEMON_STATUS'; status: AppState['daemonStatus'] }
  | { type: 'SET_WORK_DIR'; dir: string }
  | { type: 'SET_FILE_TREE'; tree: FileEntry[] }
  | { type: 'OPEN_FILE'; path: string; content: string }
  | { type: 'ADD_CHAT'; msg: ChatMessage }
  | { type: 'SET_PLAN'; markdown: string }
  | { type: 'ADD_PATCH'; patch: FilePatchPayload }
  | { type: 'CLEAR_PATCHES' }
  | { type: 'SET_VERIFICATION'; payload: AppState['verificationResult'] }
  | { type: 'ADD_LOG'; log: LogPayload }
  | { type: 'SET_ERROR'; error: AppState['error'] }
  | { type: 'RESET' }
  | { type: 'SET_CURRENT_TASK'; task: string }
  | { type: 'ACCEPT_PATCH'; path: string }
  | { type: 'REJECT_PATCH'; path: string }
  | { type: 'SET_STREAMING'; content: string }
  | { type: 'APPEND_STREAMING'; content: string }
  | { type: 'CLEAR_STREAMING' }
  | { type: 'SET_CIRCUIT_BREAK'; active: boolean; reason?: string }
  | { type: 'SET_LAST_ERROR'; message: string | null }
  | { type: 'CLEAR_ERROR' }
  | { type: 'HANDLE_AGENT_EVENT'; event: AgentEvent };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_PHASE':         return { ...state, phase: action.phase };
    case 'SET_DAEMON_STATUS': return { ...state, daemonStatus: action.status };
    case 'SET_WORK_DIR':      return { ...state, workDir: action.dir };
    case 'SET_FILE_TREE':     return { ...state, fileTree: action.tree };
    case 'OPEN_FILE':         return { ...state, openFilePath: action.path, openFileContent: action.content };
    case 'ADD_CHAT':          return { ...state, chatMessages: [...state.chatMessages, action.msg] };
    case 'SET_PLAN':          return { ...state, planMarkdown: action.markdown };
    case 'ADD_PATCH':         return { ...state, patches: [...state.patches, action.patch] };
    case 'CLEAR_PATCHES':     return { ...state, patches: [], appliedPatches: [] };
    case 'SET_VERIFICATION':  return { ...state, verificationResult: action.payload };
    case 'ADD_LOG':           return { ...state, logs: [...state.logs.slice(-500), action.log] };
    case 'SET_ERROR':         return { ...state, error: action.error, phase: 'error' };
    case 'RESET':             return { ...INITIAL_STATE, workDir: state.workDir, fileTree: state.fileTree, daemonStatus: state.daemonStatus, taskHistory: state.taskHistory };
    case 'SET_CURRENT_TASK':  return { ...state, currentTask: action.task };
    case 'ACCEPT_PATCH':      return { ...state, patches: state.patches.filter(p => p.path !== action.path), appliedPatches: [...state.appliedPatches, action.path] };
    case 'REJECT_PATCH':      return { ...state, patches: state.patches.filter(p => p.path !== action.path) };
    case 'SET_STREAMING':     return { ...state, streamingContent: action.content };
    case 'APPEND_STREAMING':  return { ...state, streamingContent: state.streamingContent + action.content };
    case 'CLEAR_STREAMING':   return { ...state, streamingContent: '' };
    case 'SET_CIRCUIT_BREAK': return { ...state, circuitBreakActive: action.active, lastError: action.reason || state.lastError, phase: action.active ? 'error' : state.phase };
    case 'SET_LAST_ERROR':    return { ...state, lastError: action.message };
    case 'CLEAR_ERROR':       return { ...state, lastError: null, circuitBreakActive: false, phase: state.phase === 'error' ? 'idle' : state.phase };
    case 'HANDLE_AGENT_EVENT': return handleAgentEvent(state, action.event);
    default: return state;
  }
}

function handleAgentEvent(state: AppState, event: AgentEvent): AppState {
  switch (event.type) {
    case 'agent_ready':       return { ...state, daemonStatus: 'running' };
    case 'task_submitted':    return { ...state, currentTask: event.task, phase: 'thinking', streamingContent: '' };
    case 'planning_started':  return { ...state, phase: 'planning' };
    case 'plan_created':      return { ...state, planMarkdown: event.markdown };
    case 'plan_approved':     return { ...state, phase: 'executing' };
    case 'execution_started': return { ...state, phase: 'executing' };
    case 'task_update':       return { ...state, streamingContent: event.markdown };
    case 'file_patch_proposed': return { ...state, patches: [...state.patches, { path: event.path, diff: event.diff, backup: '', originalContent: event.originalContent, newContent: event.newContent }] };
    case 'file_patch_accepted': return { ...state, patches: state.patches.filter(p => p.path !== event.path), appliedPatches: [...state.appliedPatches, event.path] };
    case 'file_patch_rejected': return { ...state, patches: state.patches.filter(p => p.path !== event.path) };
    case 'verification_started': return { ...state, phase: 'verifying' };
    case 'verification_completed': return { ...state, verificationResult: { passed: event.passed, markdown: event.markdown, testResults: event.testsRun !== undefined ? `${event.testsPassed}/${event.testsRun} passed` : undefined } };
    case 'streaming_chunk':   return { ...state, streamingContent: state.streamingContent + event.content };
    case 'error':             return { ...state, lastError: event.message, phase: 'error' };
    case 'circuit_break':     return { ...state, circuitBreakActive: true, lastError: event.reason, phase: 'error' };
    case 'loop_detected':     return { ...state, lastError: event.message };
    case 'daemon_health':     return { ...state, daemonStatus: event.status === 'down' ? 'error' : 'running' };
    case 'done':              return { ...state, phase: 'done' };
    default:                  return state;
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

type PanelFocus = 'explorer' | 'editor' | 'agent' | null;

export default function App() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  const [explorerWidth, setExplorerWidth]           = useState(220);
  const [agentWidth, setAgentWidth]                 = useState(360);
  const [focus, setFocus]                           = useState<PanelFocus>('editor');
  const [showSettings, setShowSettings]             = useState(false);
  const [explorerCollapsed, setExplorerCollapsed]   = useState(false);
  const [agentCollapsed, setAgentCollapsed]         = useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [showDiffReview, setShowDiffReview]         = useState(false);
  const [capabilityPanel, setCapabilityPanel]       = useState<null | 'mcp' | 'skills' | 'workflows'>(null);

  // Active skill selected from the Skills panel.
  // Shown as a chip in AgentPanel; its name is appended to submitted tasks.
  const [activeSkill, setActiveSkill] = useState<string | null>(null);

  const [theme, setTheme] = useState<'dark' | 'light'>(() =>
    (localStorage.getItem('hcode-theme') as 'dark' | 'light') || 'dark'
  );
  const [editorSettings, setEditorSettings] = useState(() => {
    try { const s = localStorage.getItem('hcode-editor-settings'); return s ? JSON.parse(s) : { fontSize: 13, wordWrap: false, minimap: true, lineNumbers: true, tabSize: 4 }; }
    catch { return { fontSize: 13, wordWrap: false, minimap: true, lineNumbers: true, tabSize: 4 }; }
  });

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); localStorage.setItem('hcode-theme', theme); }, [theme]);
  useEffect(() => { localStorage.setItem('hcode-editor-settings', JSON.stringify(editorSettings)); }, [editorSettings]);

  const recentFolders = useMemo(() => {
    try { return JSON.parse(localStorage.getItem('hcode-recent-folders') || '[]'); } catch { return []; }
  }, [state.workDir]);

  const containerRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<{ target: 'explorer' | 'agent' | null; startX: number; startWidth: number }>({ target: null, startX: 0, startWidth: 0 });

  // ── Daemon event listener ─────────────────────────────────────────────────

  useEffect(() => {
    let unlisten: (() => void) | null = null;

    ipc.onDaemonMessage((msg: HcodeMessage) => {
      switch (msg.type) {
        // ── v1 legacy events ───────────────────────────────────────────────
        case 'agent_phase':
          dispatch({ type: 'SET_PHASE', phase: msg.phase });
          dispatch({ type: 'ADD_CHAT', msg: { id: crypto.randomUUID(), type: 'agent_phase', phase: msg.phase, content: `Phase: ${msg.phase}`, timestamp: new Date().toLocaleTimeString() } });
          break;
        case 'plan':
          dispatch({ type: 'SET_PLAN', markdown: msg.payload.markdown });
          break;
        case 'file_patch':
          dispatch({ type: 'ADD_PATCH', patch: msg.payload });
          break;
        case 'verification':
          dispatch({ type: 'SET_VERIFICATION', payload: msg.payload });
          break;
        case 'task_update':
          dispatch({ type: 'SET_STREAMING', content: msg.payload.markdown });
          break;
        case 'log':
          dispatch({ type: 'ADD_LOG', log: msg.payload });
          break;
        case 'error':
          dispatch({ type: 'SET_ERROR', error: msg.payload });
          dispatch({ type: 'SET_LAST_ERROR', message: msg.payload.message });
          dispatch({ type: 'ADD_CHAT', msg: { id: crypto.randomUUID(), type: 'system', content: `Error: ${msg.payload.message}`, timestamp: new Date().toLocaleTimeString() } });
          break;
        case 'circuit_break':
          dispatch({ type: 'SET_CIRCUIT_BREAK', active: true, reason: msg.payload.reason });
          break;
        case 'ready':
          dispatch({ type: 'SET_DAEMON_STATUS', status: 'running' });
          break;
        case 'done':
          dispatch({ type: 'SET_PHASE', phase: 'done' });
          dispatch({ type: 'CLEAR_STREAMING' });
          break;
        // ── C2 streaming events (emitted by bridge.py / mock daemon) ──────
        case 'streaming_chunk':
          dispatch({ type: 'APPEND_STREAMING', content: msg.payload.content });
          break;
        case 'planning_started':
          dispatch({ type: 'SET_PHASE', phase: 'planning' });
          dispatch({ type: 'ADD_CHAT', msg: { id: crypto.randomUUID(), type: 'agent_phase', phase: 'planning', content: 'Phase: planning', timestamp: new Date().toLocaleTimeString() } });
          break;
        case 'execution_started':
          dispatch({ type: 'SET_PHASE', phase: 'executing' });
          dispatch({ type: 'ADD_CHAT', msg: { id: crypto.randomUUID(), type: 'agent_phase', phase: 'executing', content: 'Phase: executing', timestamp: new Date().toLocaleTimeString() } });
          break;
        case 'verification_started':
          dispatch({ type: 'SET_PHASE', phase: 'verifying' });
          dispatch({ type: 'ADD_CHAT', msg: { id: crypto.randomUUID(), type: 'agent_phase', phase: 'verifying', content: 'Phase: verifying', timestamp: new Date().toLocaleTimeString() } });
          break;
        case 'plan_created':
          dispatch({ type: 'SET_PLAN', markdown: msg.payload.markdown });
          break;
      }
    }).then(fn => { unlisten = fn; });

    ipc.onDaemonStatus(info => {
      dispatch({ type: 'SET_DAEMON_STATUS', status: info.status });
    });

    ipc.startDaemon().then(info => {
      dispatch({ type: 'SET_DAEMON_STATUS', status: info.status });
    }).catch(() => {
      dispatch({ type: 'SET_DAEMON_STATUS', status: 'error' });
    });

    return () => { unlisten?.(); };
  }, []);

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  const selectFolderRef = useRef<() => void>(() => {});
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'p') { e.preventDefault(); setCommandPaletteOpen(p => !p); return; }
      if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); selectFolderRef.current(); return; }
      if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'b') { e.preventDefault(); setExplorerCollapsed(p => !p); }
      if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'j') { e.preventDefault(); setAgentCollapsed(p => !p); }
      if (e.ctrlKey && e.key === ',') { e.preventDefault(); setShowSettings(p => !p); }
      if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'k') { e.preventDefault(); setAgentCollapsed(false); setFocus('agent'); }
      if (e.ctrlKey && e.key === '1') { e.preventDefault(); setFocus('explorer'); setExplorerCollapsed(false); }
      if (e.ctrlKey && e.key === '2') { e.preventDefault(); setFocus('editor'); }
      if (e.ctrlKey && e.key === '3') { e.preventDefault(); setFocus('agent'); setAgentCollapsed(false); }
      if (e.key === 'Escape') {
        if (commandPaletteOpen) { setCommandPaletteOpen(false); return; }
        if (showSettings) { setShowSettings(false); return; }
        if (showDiffReview) { setShowDiffReview(false); return; }
        setFocus('editor');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showSettings, commandPaletteOpen, showDiffReview]);

  // ── Callbacks ─────────────────────────────────────────────────────────────

  const handleSelectFolder = useCallback(async () => {
    const dir = await ipc.openFolder();
    if (dir) {
      dispatch({ type: 'SET_WORK_DIR', dir });
      const tree = await ipc.listDirectory(dir);
      dispatch({ type: 'SET_FILE_TREE', tree });
      setExplorerCollapsed(false);
    }
  }, []);
  selectFolderRef.current = handleSelectFolder;

  const handleBrowsePath = useCallback(async (path: string) => {
    const children = await ipc.listDirectory(path);
    dispatch({ type: 'SET_FILE_TREE', tree: updateTreeChildren(state.fileTree, path, children) });
  }, [state.fileTree]);

  const handleOpenFile = useCallback(async (path: string) => {
    const content = await ipc.readFile(path);
    dispatch({ type: 'OPEN_FILE', path, content });
    setFocus('editor');
  }, []);

  const handleSubmitTask = useCallback(async (task: string, mode: 'planning' | 'fast') => {
    dispatch({ type: 'CLEAR_PATCHES' });
    dispatch({ type: 'SET_PLAN', markdown: '' });
    dispatch({ type: 'SET_VERIFICATION', payload: null });
    dispatch({ type: 'CLEAR_STREAMING' });
    dispatch({ type: 'SET_LAST_ERROR', message: null });
    dispatch({ type: 'SET_CIRCUIT_BREAK', active: false });
    dispatch({ type: 'SET_PHASE', phase: 'thinking' });
    dispatch({ type: 'SET_CURRENT_TASK', task });
    dispatch({ type: 'ADD_CHAT', msg: { id: crypto.randomUUID(), type: 'user', content: task, timestamp: new Date().toLocaleTimeString() } });
    setShowDiffReview(false);
    try { await ipc.runTask(task, mode, false); }
    catch (err) { dispatch({ type: 'SET_LAST_ERROR', message: err instanceof Error ? err.message : 'Failed to submit task' }); }
    setFocus('agent');
  }, []);

  const handleApprovePlan = useCallback(async () => { dispatch({ type: 'SET_PHASE', phase: 'executing' }); await ipc.approvePlan(); }, []);
  const handleRejectPlan  = useCallback(async () => { dispatch({ type: 'SET_PHASE', phase: 'idle' }); await ipc.rejectPlan('User rejected plan'); }, []);

  const handleFileDecision = useCallback(async (path: string, accepted: boolean) => {
    try {
      if (accepted) { await ipc.acceptPatch(path); dispatch({ type: 'ACCEPT_PATCH', path }); }
      else          { await ipc.rejectPatch(path); dispatch({ type: 'REJECT_PATCH', path }); }
    } catch (err) { dispatch({ type: 'SET_LAST_ERROR', message: `Patch failed: ${err instanceof Error ? err.message : String(err)}` }); }
  }, []);

  const handleRollback = useCallback(async () => {
    try { await ipc.rollbackAll(); dispatch({ type: 'CLEAR_PATCHES' }); setShowDiffReview(false); }
    catch (err) { dispatch({ type: 'SET_LAST_ERROR', message: `Rollback failed: ${err instanceof Error ? err.message : String(err)}` }); }
  }, []);

  const handleReviewDiffs = useCallback(() => { setShowDiffReview(true); setFocus('editor'); }, []);

  // ── Command registry ──────────────────────────────────────────────────────

  const commands: Command[] = useMemo(() => [
    { id: 'open-folder',     label: 'Open Folder',          category: 'File',         shortcut: 'Ctrl+O', action: handleSelectFolder },
    { id: 'toggle-explorer', label: 'Toggle Explorer',      category: 'View',         shortcut: 'Ctrl+B', action: () => setExplorerCollapsed(p => !p) },
    { id: 'toggle-agent',    label: 'Toggle Agent Panel',   category: 'View',         shortcut: 'Ctrl+J', action: () => setAgentCollapsed(p => !p) },
    { id: 'open-settings',   label: 'Open Settings',        category: 'Preferences',  shortcut: 'Ctrl+,', action: () => setShowSettings(true) },
    { id: 'new-task',        label: 'New Task',             category: 'Agent',        shortcut: 'Ctrl+K', action: () => { setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'toggle-theme',    label: 'Toggle Theme',         category: 'Preferences',  action: () => setTheme(t => t === 'dark' ? 'light' : 'dark') },
    { id: 'focus-explorer',  label: 'Focus Explorer',       category: 'View',         shortcut: 'Ctrl+1', action: () => { setFocus('explorer'); setExplorerCollapsed(false); } },
    { id: 'focus-editor',    label: 'Focus Editor',         category: 'View',         shortcut: 'Ctrl+2', action: () => setFocus('editor') },
    { id: 'focus-agent',     label: 'Focus Agent',          category: 'View',         shortcut: 'Ctrl+3', action: () => { setFocus('agent'); setAgentCollapsed(false); } },
    { id: 'review-diffs',    label: 'Review Pending Diffs', category: 'Agent',        action: handleReviewDiffs },
    { id: 'open-mcp',        label: 'Show MCP Servers',     category: 'Capabilities', action: () => { setCapabilityPanel('mcp'); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'open-skills',     label: 'Show Skills',          category: 'Capabilities', action: () => { setCapabilityPanel('skills'); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'open-workflows',  label: 'Show Workflows',       category: 'Capabilities', action: () => { setCapabilityPanel('workflows'); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'open-agent-stream', label: 'Show Agent Stream',  category: 'Capabilities', action: () => { setCapabilityPanel(null); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'abort-task',      label: 'Abort Current Task',   category: 'Agent',        action: async () => { await ipc.abortTask(); dispatch({ type: 'CLEAR_ERROR' }); dispatch({ type: 'SET_PHASE', phase: 'idle' }); dispatch({ type: 'CLEAR_STREAMING' }); } },
    { id: 'clear-errors',    label: 'Clear Errors',         category: 'Agent',        action: () => dispatch({ type: 'CLEAR_ERROR' }) },
  ], [handleSelectFolder, handleReviewDiffs]);

  // ── Drag logic ────────────────────────────────────────────────────────────

  const onDragStart = (target: 'explorer' | 'agent', e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    dragState.current = { target, startX: e.clientX, startWidth: target === 'explorer' ? explorerWidth : agentWidth };
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
  };

  const onDragMove = (e: MouseEvent) => {
    const s = dragState.current;
    if (!s.target) return;
    const delta = e.clientX - s.startX;
    if (s.target === 'explorer') setExplorerWidth(Math.max(160, Math.min(480, s.startWidth + delta)));
    else setAgentWidth(Math.max(280, Math.min(600, s.startWidth - delta)));
  };

  const onDragEnd = () => {
    dragState.current.target = null;
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="hcode-app" ref={containerRef}>
      <CommandPalette isOpen={commandPaletteOpen} onClose={() => setCommandPaletteOpen(false)} commands={commands} />

      <div className="hcode-titlebar">
        <span className="hcode-titlebar__logo">Hcode v2</span>
        <span className="hcode-titlebar__project">
          {state.workDir ? state.workDir.split(/[\\/]/).pop() : 'No project open'}
        </span>
        {state.workDir && <span className="hcode-titlebar__path" title={state.workDir}>{state.workDir}</span>}
      </div>

      <div className="hcode-main">
        {/* Explorer */}
        {!explorerCollapsed ? (
          <div className={`hcode-panel hcode-explorer ${focus === 'explorer' ? 'has-focus' : ''}`} style={{ width: `${explorerWidth}px` }} onClickCapture={() => setFocus('explorer')}>
            <div className="hcode-panel-header">
              <span className="hcode-explorer-title">Explorer</span>
              <button className="hcode-btn hcode-btn--ghost hcode-btn--small" onClick={e => { e.stopPropagation(); setExplorerCollapsed(true); }} title="Collapse (Ctrl+B)" style={{ padding: '2px var(--space-1)' }}>■</button>
            </div>
            <FileExplorer workDir={state.workDir} fileTree={state.fileTree} onSelectFolder={handleSelectFolder} onBrowsePath={handleBrowsePath} onOpenFile={handleOpenFile} />
          </div>
        ) : (
          <div className="hcode-panel-collapsed-strip" onClick={() => setExplorerCollapsed(false)} title="Expand Explorer (Ctrl+B)" style={{ width: '36px', borderRight: '1px solid var(--border-default)', background: 'var(--surface-1)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', letterSpacing: '2px', textTransform: 'uppercase' }}>Explorer</span>
          </div>
        )}

        {!explorerCollapsed && <div className="hcode-resize-handle" onPointerDown={e => onDragStart('explorer', e)} onDoubleClick={() => setExplorerWidth(220)} />}

        {/* Editor */}
        <div className={`hcode-panel hcode-editor-panel ${focus === 'editor' ? 'has-focus' : ''}`} onClickCapture={() => setFocus('editor')}>
          {showDiffReview && state.patches.length > 0 ? (
            <div style={{ padding: 'var(--space-4)', flex: 1, overflowY: 'auto' }}>
              <DiffReviewer patches={state.patches} appliedPatches={state.appliedPatches} onFileDecision={handleFileDecision} onRollback={handleRollback} lastError={state.lastError} />
            </div>
          ) : state.openFilePath ? (
            <>
              <div className="hcode-panel-header hcode-editor-tabs">
                <button className="hcode-editor-tab hcode-editor-tab--active">{state.openFilePath.split(/[\\/]/).pop()}</button>
              </div>
              <div className="hcode-breadcrumbs">
                {state.openFilePath.split(/[\\/]/).map((seg, i, arr) => (
                  <span key={i} className="hcode-breadcrumbs__segment">
                    {i > 0 && <span className="hcode-breadcrumbs__sep">›</span>}
                    <span className={i === arr.length - 1 ? 'hcode-breadcrumbs__current' : ''}>{seg}</span>
                  </span>
                ))}
              </div>
              <div className="hcode-editor-body">
                <MonacoEditor filePath={state.openFilePath} content={state.openFileContent} theme={theme} settings={editorSettings} onChange={() => {}} />
              </div>
            </>
          ) : (
            <WelcomeScreen onOpenFolder={handleSelectFolder} onNewTask={() => { setAgentCollapsed(false); setFocus('agent'); }} onOpenSettings={() => setShowSettings(true)} recentFolders={recentFolders} onOpenRecent={() => handleSelectFolder()} />
          )}
        </div>

        {!agentCollapsed && <div className="hcode-resize-handle" onPointerDown={e => onDragStart('agent', e)} onDoubleClick={() => setAgentWidth(360)} />}

        {agentCollapsed && (
          <div className="hcode-panel-collapsed-strip" onClick={() => setAgentCollapsed(false)} title="Expand Agent (Ctrl+J)" style={{ width: '36px', borderLeft: '1px solid var(--border-default)', background: 'var(--surface-1)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ writingMode: 'vertical-rl', fontSize: 'var(--text-xs)', color: 'var(--fg-secondary)', letterSpacing: '2px', textTransform: 'uppercase' }}>Agent</span>
          </div>
        )}

        {/* Agent Panel */}
        {!agentCollapsed && (
          <div className={`hcode-panel hcode-agent-panel ${focus === 'agent' ? 'has-focus' : ''}`} style={{ width: `${agentWidth}px` }} onClickCapture={() => setFocus('agent')}>
            {showSettings ? (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                <div className="hcode-panel-header hcode-agent-header">
                  <span className="hcode-agent-title">SETTINGS</span>
                  <div className="hcode-agent-actions"><span className="hcode-agent-icon" onClick={() => setShowSettings(false)} title="Close">✕</span></div>
                </div>
                <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-4)' }}>
                  <SettingsPanel theme={theme} onThemeChange={setTheme} editorSettings={editorSettings} onEditorSettingsChange={setEditorSettings} />
                </div>
              </div>
            ) : capabilityPanel === 'mcp' ? (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                <div className="hcode-panel-header hcode-agent-header">
                  <span className="hcode-agent-title">MCP SERVERS</span>
                  <div className="hcode-agent-actions"><span className="hcode-agent-icon" onClick={() => setCapabilityPanel(null)} title="Back">✕</span></div>
                </div>
                <div style={{ flex: 1, overflowY: 'auto' }}><MCPPanel /></div>
              </div>
            ) : capabilityPanel === 'skills' ? (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                <div className="hcode-panel-header hcode-agent-header">
                  <span className="hcode-agent-title">SKILLS</span>
                  <div className="hcode-agent-actions"><span className="hcode-agent-icon" onClick={() => setCapabilityPanel(null)} title="Back">✕</span></div>
                </div>
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  <SkillsPanel
                    activeSkill={activeSkill}
                    onSelect={name => {
                      setActiveSkill(name);
                      setCapabilityPanel(null); // return to agent stream view
                      setAgentCollapsed(false);
                      setFocus('agent');
                    }}
                  />
                </div>
              </div>
            ) : capabilityPanel === 'workflows' ? (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                <div className="hcode-panel-header hcode-agent-header">
                  <span className="hcode-agent-title">WORKFLOWS</span>
                  <div className="hcode-agent-actions"><span className="hcode-agent-icon" onClick={() => setCapabilityPanel(null)} title="Back">✕</span></div>
                </div>
                <div style={{ flex: 1, overflowY: 'auto' }}><WorkflowsPanel /></div>
              </div>
            ) : (
              <AgentPanel
                appState={state}
                onSubmitTask={handleSubmitTask}
                onApprovePlan={handleApprovePlan}
                onRejectPlan={handleRejectPlan}
                onSettingsClick={() => setShowSettings(true)}
                onCollapseClick={() => setAgentCollapsed(true)}
                onReviewDiffs={handleReviewDiffs}
                onAbortTask={async () => { await ipc.abortTask(); dispatch({ type: 'CLEAR_ERROR' }); dispatch({ type: 'SET_PHASE', phase: 'idle' }); dispatch({ type: 'CLEAR_STREAMING' }); }}
                onClearError={() => dispatch({ type: 'CLEAR_ERROR' })}
                activeSkill={activeSkill}
                onDismissSkill={() => setActiveSkill(null)}
              />
            )}
          </div>
        )}
      </div>

      <StatusBar daemonStatus={state.daemonStatus} phase={state.phase} workDir={state.workDir} pendingPatchCount={state.patches.length} circuitBreakActive={state.circuitBreakActive} currentTask={state.currentTask} />
    </div>
  );
}

function updateTreeChildren(tree: FileEntry[], parentPath: string, children: FileEntry[]): FileEntry[] {
  return tree.map(entry => {
    if (entry.path === parentPath) return { ...entry, children };
    if (entry.children) return { ...entry, children: updateTreeChildren(entry.children, parentPath, children) };
    return entry;
  });
}
