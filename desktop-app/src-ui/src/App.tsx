/**
 * App.tsx — HCode v2 desktop shell.
 *
 * v3 (agent-chat rebuild):
 *  - The agent surface is now a CONVERSATION of turns[] (see types.ts / AgentPanel).
 *    handleSubmitTask APPENDS a turn; nothing is wiped on submit or done.
 *  - Daemon events route to the active (last) turn via applyAgentMessage — the
 *    full bridge vocabulary (planning/execution/verification_started,
 *    streaming_chunk, plan_created, task_update incl. the lsp_verify lane,
 *    file_patch, verification, error, done).
 *  - The daemon-message listener effect is StrictMode-safe (no double-register).
 *  - Errors are a single per-turn surface; phase 'error' re-enables the composer.
 */
import React, { useReducer, useEffect, useCallback, useRef, useState, useMemo } from 'react';
import type { AppState, AgentPhase, HcodeMessage, FileEntry, Turn } from './types';
import { INITIAL_STATE, createTurn } from './types';
import { applyAgentMessage } from './utils/applyAgentMessage';

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
  | { type: 'SET_DAEMON_STATUS'; status: AppState['daemonStatus'] }
  | { type: 'SET_WORK_DIR'; dir: string }
  | { type: 'SET_FILE_TREE'; tree: FileEntry[] }
  | { type: 'OPEN_FILE'; path: string; content: string }
  | { type: 'START_TURN'; turn: Turn }
  | { type: 'AGENT_MSG'; msg: HcodeMessage }
  | { type: 'PATCH_DECISION'; turnId: string; path: string; accepted: boolean }
  | { type: 'ROLLBACK_TURN'; turnId: string }
  | { type: 'SET_REVIEW_TURN'; turnId: string | null }
  | { type: 'ABORT_ACTIVE' }
  | { type: 'CLEAR_TURN_ERROR'; turnId: string }
  | { type: 'CLEAR_CONVERSATION' };

const BUSY_PHASES: AgentPhase[] = ['thinking', 'planning', 'executing', 'verifying'];

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_DAEMON_STATUS': return { ...state, daemonStatus: action.status };
    case 'SET_WORK_DIR':      return { ...state, workDir: action.dir };
    case 'SET_FILE_TREE':     return { ...state, fileTree: action.tree };
    case 'OPEN_FILE':         return { ...state, openFilePath: action.path, openFileContent: action.content };

    case 'START_TURN':
      return { ...state, turns: [...state.turns, action.turn], taskHistory: [...state.taskHistory, action.turn.userMessage] };

    case 'AGENT_MSG': {
      // Every agent event applies to the active (last) turn.
      if (state.turns.length === 0) return state;
      const i = state.turns.length - 1;
      const updated = applyAgentMessage(state.turns[i], action.msg);
      if (updated === state.turns[i]) return state;
      const turns = state.turns.slice();
      turns[i] = updated;
      return { ...state, turns };
    }

    case 'PATCH_DECISION':
      return { ...state, turns: state.turns.map(t => t.id !== action.turnId ? t : {
        ...t,
        patches: t.patches.filter(p => p.path !== action.path),
        appliedPatches: action.accepted ? [...t.appliedPatches, action.path] : t.appliedPatches,
        rejectedPatches: action.accepted ? t.rejectedPatches : [...t.rejectedPatches, action.path],
      }) };

    case 'ROLLBACK_TURN':
      return { ...state, turns: state.turns.map(t => t.id !== action.turnId ? t : {
        ...t,
        rejectedPatches: [...t.rejectedPatches, ...t.patches.map(p => p.path)],
        patches: [],
      }) };

    case 'SET_REVIEW_TURN': return { ...state, reviewTurnId: action.turnId };

    case 'ABORT_ACTIVE': {
      if (state.turns.length === 0) return state;
      const i = state.turns.length - 1;
      const last = state.turns[i];
      if (last.phase === 'done' || last.phase === 'error') return state;
      const turns = state.turns.slice();
      turns[i] = { ...last, phase: 'done', answer: last.answer || '_(aborted)_' };
      return { ...state, turns };
    }

    case 'CLEAR_TURN_ERROR':
      return { ...state, turns: state.turns.map(t => t.id !== action.turnId ? t : {
        ...t, error: null, phase: t.phase === 'error' ? 'done' : t.phase,
      }) };

    case 'CLEAR_CONVERSATION': return { ...state, turns: [], reviewTurnId: null };

    default: return state;
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

type PanelFocus = 'explorer' | 'editor' | 'agent' | null;

export default function App() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  const [explorerWidth, setExplorerWidth]           = useState(220);
  const [agentWidth, setAgentWidth]                 = useState(400);
  const [focus, setFocus]                           = useState<PanelFocus>('editor');
  const [showSettings, setShowSettings]             = useState(false);
  const [explorerCollapsed, setExplorerCollapsed]   = useState(false);
  const [agentCollapsed, setAgentCollapsed]         = useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [showDiffReview, setShowDiffReview]         = useState(false);
  const [capabilityPanel, setCapabilityPanel]       = useState<null | 'mcp' | 'skills' | 'workflows'>(null);

  // Active skill selected from the Skills panel; the composer appends its name.
  const [activeSkill, setActiveSkill] = useState<string | null>(null);
  // Filesystem/OS-action errors (folder/file). Kept OUT of the conversation —
  // they are app-level, not part of any agent turn.
  const [fsError, setFsError] = useState<string | null>(null);

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

  // ── Derived conversation state ────────────────────────────────────────────
  const activeTurn = state.turns.length ? state.turns[state.turns.length - 1] : null;
  const phase: AgentPhase = activeTurn ? activeTurn.phase : 'idle';
  const isBusy = activeTurn ? BUSY_PHASES.includes(activeTurn.phase) : false;
  const reviewTurn = state.reviewTurnId ? state.turns.find(t => t.id === state.reviewTurnId) ?? null : null;
  const pendingPatchCount = useMemo(() => state.turns.reduce((n, t) => n + t.patches.length, 0), [state.turns]);

  // ── Daemon event listener (StrictMode-safe: no double-register) ───────────
  useEffect(() => {
    let cancelled = false;
    const offs: Array<() => void> = [];
    // If cleanup runs before a listen() promise resolves (StrictMode mounts the
    // effect twice), unlisten the moment it resolves so we never leak a second
    // listener — that double-registration was a root cause of duplicated events.
    const track = (p: Promise<() => void>) => {
      p.then(off => { if (cancelled) off(); else offs.push(off); }).catch(() => {});
    };

    const handleMessage = (msg: HcodeMessage) => {
      if (msg.type === 'ready') { dispatch({ type: 'SET_DAEMON_STATUS', status: 'running' }); return; }
      dispatch({ type: 'AGENT_MSG', msg });
    };

    track(ipc.onDaemonMessage(handleMessage));
    track(ipc.onDaemonStatus(info => dispatch({ type: 'SET_DAEMON_STATUS', status: info.status })));
    ipc.startDaemon()
      .then(info => dispatch({ type: 'SET_DAEMON_STATUS', status: info.status }))
      .catch(() => dispatch({ type: 'SET_DAEMON_STATUS', status: 'error' }));

    return () => { cancelled = true; offs.forEach(off => off()); };
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
    try {
      const dir = await ipc.openFolder();
      if (!dir) return; // user cancelled
      setFsError(null);
      dispatch({ type: 'SET_WORK_DIR', dir });
      dispatch({ type: 'SET_FILE_TREE', tree: [] }); // clear stale tree immediately
      try {
        const tree = await ipc.listDirectory(dir);
        dispatch({ type: 'SET_FILE_TREE', tree });
      } catch (fsErr) {
        setFsError(`Cannot read folder: ${fsErr instanceof Error ? fsErr.message : String(fsErr)}`);
        dispatch({ type: 'SET_FILE_TREE', tree: [] });
      }
      setExplorerCollapsed(false);
    } catch (err) {
      setFsError(`Open folder failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);
  selectFolderRef.current = handleSelectFolder;

  const handleBrowsePath = useCallback(async (path: string) => {
    try {
      const children = await ipc.listDirectory(path);
      dispatch({ type: 'SET_FILE_TREE', tree: updateTreeChildren(state.fileTree, path, children) });
    } catch {
      // Silently ignore expand errors — the tree item stays collapsed
    }
  }, [state.fileTree]);

  const handleOpenFile = useCallback(async (path: string) => {
    try {
      const content = await ipc.readFile(path);
      dispatch({ type: 'OPEN_FILE', path, content });
      setFocus('editor');
    } catch (err) {
      setFsError(`Cannot read file: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  const handleSubmitTask = useCallback(async (task: string, mode: 'planning' | 'fast') => {
    const turn = createTurn(task, mode);
    dispatch({ type: 'START_TURN', turn });   // APPEND — prior turns stay
    setShowDiffReview(false);
    setFocus('agent');
    try {
      await ipc.runTask(task, mode, false);
    } catch (err) {
      // Surface a submit failure as the turn's single error (no chat-line spam).
      dispatch({ type: 'AGENT_MSG', msg: { type: 'error', payload: { message: err instanceof Error ? err.message : 'Failed to submit task', suggestion: '' } } });
    }
  }, []);

  const handleFileDecision = useCallback(async (turnId: string, path: string, accepted: boolean) => {
    try { if (accepted) await ipc.acceptPatch(path); else await ipc.rejectPatch(path); }
    catch { /* mock no-op / best-effort */ }
    dispatch({ type: 'PATCH_DECISION', turnId, path, accepted });
  }, []);

  const handleReviewDiffs = useCallback((turnId: string) => {
    dispatch({ type: 'SET_REVIEW_TURN', turnId });
    setShowDiffReview(true);
    setFocus('editor');
  }, []);

  const handleRollback = useCallback(async () => {
    try { await ipc.rollbackAll(); } catch { /* best-effort */ }
    if (state.reviewTurnId) dispatch({ type: 'ROLLBACK_TURN', turnId: state.reviewTurnId });
    setShowDiffReview(false);
    dispatch({ type: 'SET_REVIEW_TURN', turnId: null });
  }, [state.reviewTurnId]);

  const handleAbort = useCallback(async () => {
    try { await ipc.abortTask(); } catch { /* best-effort */ }
    dispatch({ type: 'ABORT_ACTIVE' });
  }, []);

  // ── Command registry ──────────────────────────────────────────────────────

  const commands: Command[] = useMemo(() => [
    { id: 'open-folder',       label: 'Open Folder',          category: 'File',         shortcut: 'Ctrl+O', action: handleSelectFolder },
    { id: 'toggle-explorer',   label: 'Toggle Explorer',      category: 'View',         shortcut: 'Ctrl+B', action: () => setExplorerCollapsed(p => !p) },
    { id: 'toggle-agent',      label: 'Toggle Agent Panel',   category: 'View',         shortcut: 'Ctrl+J', action: () => setAgentCollapsed(p => !p) },
    { id: 'open-settings',     label: 'Open Settings',        category: 'Preferences',  shortcut: 'Ctrl+,', action: () => setShowSettings(true) },
    { id: 'new-task',          label: 'New Task',             category: 'Agent',        shortcut: 'Ctrl+K', action: () => { setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'new-conversation',  label: 'New Conversation',     category: 'Agent',        action: () => dispatch({ type: 'CLEAR_CONVERSATION' }) },
    { id: 'toggle-theme',      label: 'Toggle Theme',         category: 'Preferences',  action: () => setTheme(t => t === 'dark' ? 'light' : 'dark') },
    { id: 'focus-explorer',    label: 'Focus Explorer',       category: 'View',         shortcut: 'Ctrl+1', action: () => { setFocus('explorer'); setExplorerCollapsed(false); } },
    { id: 'focus-editor',      label: 'Focus Editor',         category: 'View',         shortcut: 'Ctrl+2', action: () => setFocus('editor') },
    { id: 'focus-agent',       label: 'Focus Agent',          category: 'View',         shortcut: 'Ctrl+3', action: () => { setFocus('agent'); setAgentCollapsed(false); } },
    { id: 'review-diffs',      label: 'Review Pending Diffs', category: 'Agent',        action: () => { const t = [...state.turns].reverse().find(t => t.patches.length > 0); if (t) handleReviewDiffs(t.id); } },
    { id: 'open-mcp',          label: 'Show MCP Servers',     category: 'Capabilities', action: () => { setCapabilityPanel('mcp'); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'open-skills',       label: 'Show Skills',          category: 'Capabilities', action: () => { setCapabilityPanel('skills'); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'open-workflows',    label: 'Show Workflows',       category: 'Capabilities', action: () => { setCapabilityPanel('workflows'); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'open-agent-stream', label: 'Show Conversation',    category: 'Capabilities', action: () => { setCapabilityPanel(null); setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'abort-task',        label: 'Abort Current Task',   category: 'Agent',        action: handleAbort },
    { id: 'clear-errors',      label: 'Clear Error',          category: 'Agent',        action: () => { if (activeTurn?.error) dispatch({ type: 'CLEAR_TURN_ERROR', turnId: activeTurn.id }); } },
  ], [handleSelectFolder, handleReviewDiffs, handleAbort, state.turns, activeTurn]);

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
    else setAgentWidth(Math.max(300, Math.min(720, s.startWidth - delta)));
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

      {fsError && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--space-2)',
          padding: 'var(--space-1) var(--space-3)', background: 'hsla(0, 65%, 52%, 0.10)',
          borderBottom: '1px solid var(--semantic-error)', color: 'var(--semantic-error)', fontSize: 'var(--text-xs)',
        }}>
          <span>⚠ {fsError}</span>
          <button onClick={() => setFsError(null)} title="Dismiss" style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>✕</button>
        </div>
      )}

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
          {showDiffReview && reviewTurn && reviewTurn.patches.length > 0 ? (
            <div style={{ padding: 'var(--space-4)', flex: 1, overflowY: 'auto' }}>
              <DiffReviewer patches={reviewTurn.patches} appliedPatches={reviewTurn.appliedPatches} onFileDecision={(p, a) => handleFileDecision(reviewTurn.id, p, a)} onRollback={handleRollback} lastError={null} />
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

        {!agentCollapsed && <div className="hcode-resize-handle" onPointerDown={e => onDragStart('agent', e)} onDoubleClick={() => setAgentWidth(400)} />}

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
                      setCapabilityPanel(null); // return to conversation view
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
                turns={state.turns}
                isBusy={isBusy}
                onSubmitTask={handleSubmitTask}
                onReviewDiffs={handleReviewDiffs}
                onFileDecision={handleFileDecision}
                onDismissError={(turnId) => dispatch({ type: 'CLEAR_TURN_ERROR', turnId })}
                onNewConversation={() => dispatch({ type: 'CLEAR_CONVERSATION' })}
                onSettingsClick={() => setShowSettings(true)}
                onCollapseClick={() => setAgentCollapsed(true)}
                activeSkill={activeSkill}
                onDismissSkill={() => setActiveSkill(null)}
              />
            )}
          </div>
        )}
      </div>

      <StatusBar daemonStatus={state.daemonStatus} phase={phase} workDir={state.workDir} pendingPatchCount={pendingPatchCount} circuitBreakActive={activeTurn?.phase === 'error'} currentTask={activeTurn?.userMessage ?? null} />
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
