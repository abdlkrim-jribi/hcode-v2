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
import type { ModelRow } from './ipc/bridge';

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
  | { type: 'PLAN_REVIEWED'; turnId: string }
  | { type: 'CLEAR_TURN_ERROR'; turnId: string }
  | { type: 'REMOVE_TURN'; id: string }
  | { type: 'SET_SESSIONS'; sessions: string[] }
  | { type: 'NEW_SESSION'; id: string }
  | { type: 'SWITCH_SESSION'; id: string }
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
      // Also clear awaitingReview: aborting a run paused for plan review must
      // dismiss the stale Accept/Reject buttons, not leave them dangling.
      turns[i] = { ...last, phase: 'done', answer: last.answer || '_(aborted)_', awaitingReview: false };
      return { ...state, turns };
    }

    case 'PLAN_REVIEWED':
      // Optimistic: hide the Accept/Reject buttons the instant one is clicked, so
      // a slow daemon round-trip can't produce a double-submit. The authoritative
      // state still arrives via execution_started (accept) / plan_rejected (reject).
      return { ...state, turns: state.turns.map(t => t.id !== action.turnId ? t : {
        ...t, awaitingReview: false,
      }) };

    case 'CLEAR_TURN_ERROR':
      return { ...state, turns: state.turns.map(t => t.id !== action.turnId ? t : {
        ...t, error: null, phase: t.phase === 'error' ? 'done' : t.phase,
      }) };

    case 'REMOVE_TURN':
      return { ...state, turns: state.turns.filter(t => t.id !== action.id) };

    case 'SET_SESSIONS':
      return { ...state, sessions: action.sessions };

    case 'NEW_SESSION': {
      // Archive the current transcript, then start an empty one bound to the new id.
      const archivedTurns = state.turns.length
        ? { ...state.archivedTurns, [state.currentSessionId]: state.turns }
        : state.archivedTurns;
      return { ...state, archivedTurns, currentSessionId: action.id, turns: [], reviewTurnId: null };
    }

    case 'SWITCH_SESSION': {
      if (action.id === state.currentSessionId) return state;
      // Archive the current turns; restore the target's in-run turns (or empty).
      const archivedTurns = { ...state.archivedTurns };
      if (state.turns.length) archivedTurns[state.currentSessionId] = state.turns;
      const turns = archivedTurns[action.id] ?? [];
      delete archivedTurns[action.id];   // it is live again, not archived
      return { ...state, archivedTurns, currentSessionId: action.id, turns, reviewTurnId: null };
    }

    case 'CLEAR_CONVERSATION': return { ...state, turns: [], reviewTurnId: null };

    default: return state;
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

type PanelFocus = 'explorer' | 'editor' | 'agent' | null;

// Session id persistence — uses the app's existing localStorage convention
// (same as hcode-theme / hcode-editor-settings), guarded for sandboxed storage.
const SESSION_KEY = 'hcode-session-id';
function newSessionId(): string { return `gui_${Date.now().toString(36)}`; }
function loadSessionId(): string {
  try { const v = localStorage.getItem(SESSION_KEY); if (v) return v; } catch { /* storage unavailable */ }
  return newSessionId();
}

export default function App() {
  // Lazy init so currentSessionId is restored (or generated) before first render.
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE, (init) => ({ ...init, currentSessionId: loadSessionId() }));

  const [explorerWidth, setExplorerWidth]           = useState(220);
  const [agentWidth, setAgentWidth]                 = useState(400);
  const [focus, setFocus]                           = useState<PanelFocus>('editor');
  const [showSettings, setShowSettings]             = useState(false);
  const [explorerCollapsed, setExplorerCollapsed]   = useState(false);
  const [agentCollapsed, setAgentCollapsed]         = useState(false);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [showDiffReview, setShowDiffReview]         = useState(false);
  const [capabilityPanel, setCapabilityPanel]       = useState<null | 'mcp' | 'skills' | 'workflows'>(null);

  // Single-skill composer hint (unchanged feature — appends "[Preferred skill: X]" to task text).
  const [activeSkill, setActiveSkill] = useState<string | null>(null);
  // Multi-select: which skills the agent loads. null = all (default). Set = specific subset.
  const [activeSkills, setActiveSkills] = useState<Set<string> | null>(null);
  // Live model catalog (free + tool-capable) from the daemon, for the header
  // dropdown. Fetched once on mount; falls back to the .env model if the provider
  // is unreachable, so it's never empty for long.
  const [models, setModels] = useState<ModelRow[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  // Per-session model choice keyed by thread_id (model id; absent = .env default).
  // Same localStorage convention as session names — the thread_id is unchanged, so
  // daemon memory is unaffected; only which model the next task runs against.
  const [sessionModels, setSessionModels] = useState<Record<string, string>>(() => {
    try { const s = localStorage.getItem('hcode-session-models'); return s ? JSON.parse(s) : {}; }
    catch { return {}; }
  });
  // Per-session plan-review toggle (HITL). Default off = today's run-through. Same
  // localStorage convention; keyed by thread_id so it survives reloads per session.
  const [sessionPlanReview, setSessionPlanReview] = useState<Record<string, boolean>>(() => {
    try { const s = localStorage.getItem('hcode-session-plan-review'); return s ? JSON.parse(s) : {}; }
    catch { return {}; }
  });
  // Per-session composer mode (Plan vs Fast), keyed by thread_id. "planning"
  // forces the full plan→execute→verify arc; "fast" (absent default here matches
  // the daemon's absent→classifier behaviour). Same localStorage convention so a
  // session's mode survives reloads and session switches.
  const [sessionMode, setSessionMode] = useState<Record<string, 'planning' | 'fast'>>(() => {
    try { const s = localStorage.getItem('hcode-session-mode'); return s ? JSON.parse(s) : {}; }
    catch { return {}; }
  });
  // Filesystem/OS-action errors (folder/file). Kept OUT of the conversation —
  // they are app-level, not part of any agent turn.
  const [fsError, setFsError] = useState<string | null>(null);
  // Non-destructive transient notice (e.g. single-flight "a task is already running").
  const [notice, setNotice] = useState<string | null>(null);

  const [theme, setTheme] = useState<'dark' | 'light'>(() =>
    (localStorage.getItem('hcode-theme') as 'dark' | 'light') || 'dark'
  );
  const [editorSettings, setEditorSettings] = useState(() => {
    try { const s = localStorage.getItem('hcode-editor-settings'); return s ? JSON.parse(s) : { fontSize: 13, wordWrap: false, minimap: true, lineNumbers: true, tabSize: 4 }; }
    catch { return { fontSize: 13, wordWrap: false, minimap: true, lineNumbers: true, tabSize: 4 }; }
  });
  // Friendly display names per session, keyed by thread_id. The thread_id never
  // changes (so daemon memory stays intact); only the label does. Same
  // localStorage convention as hcode-theme / hcode-session-id. The dropdown
  // falls back to the thread_id when a session has no custom name.
  const [sessionNames, setSessionNames] = useState<Record<string, string>>(() => {
    try { const s = localStorage.getItem('hcode-session-names'); return s ? JSON.parse(s) : {}; }
    catch { return {}; }
  });

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); localStorage.setItem('hcode-theme', theme); }, [theme]);
  useEffect(() => { localStorage.setItem('hcode-editor-settings', JSON.stringify(editorSettings)); }, [editorSettings]);
  useEffect(() => { try { localStorage.setItem('hcode-session-names', JSON.stringify(sessionNames)); } catch { /* storage unavailable */ } }, [sessionNames]);
  useEffect(() => { try { localStorage.setItem('hcode-session-models', JSON.stringify(sessionModels)); } catch { /* storage unavailable */ } }, [sessionModels]);
  useEffect(() => { try { localStorage.setItem('hcode-session-plan-review', JSON.stringify(sessionPlanReview)); } catch { /* storage unavailable */ } }, [sessionPlanReview]);
  useEffect(() => { try { localStorage.setItem('hcode-session-mode', JSON.stringify(sessionMode)); } catch { /* storage unavailable */ } }, [sessionMode]);
  // Fetch the live model catalog once on mount. listModels never throws an empty
  // result (the daemon falls back to the .env model), but guard anyway so a
  // transport failure just leaves the dropdown on "default".
  useEffect(() => {
    let alive = true;
    setModelsLoading(true);
    ipc.listModels()
      .then(m => { if (alive) setModels(m); })
      .catch(() => { /* leave models empty → dropdown shows only the default */ })
      .finally(() => { if (alive) setModelsLoading(false); });
    return () => { alive = false; };
  }, []);
  // Persist the active session id; auto-dismiss notices; fetch the session list on mount.
  useEffect(() => { try { localStorage.setItem(SESSION_KEY, state.currentSessionId); } catch { /* ignore */ } }, [state.currentSessionId]);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(null), 4500); return () => clearTimeout(t); }, [notice]);
  useEffect(() => { ipc.listSessions().then(s => dispatch({ type: 'SET_SESSIONS', sessions: s })).catch(() => {}); }, []);

  const recentFolders = useMemo(() => {
    try { return JSON.parse(localStorage.getItem('hcode-recent-folders') || '[]'); } catch { return []; }
  }, [state.workDir]);

  const containerRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<{ target: 'explorer' | 'agent' | null; startX: number; startWidth: number }>({ target: null, startX: 0, startWidth: 0 });

  // Latest workDir + fileTree for the daemon-message listener, which is set up
  // once with [] deps and would otherwise close over stale values. Used to
  // auto-refresh the explorer when a task completes (see the 'done' handler).
  const workDirRef = useRef(state.workDir);
  const fileTreeRef = useRef(state.fileTree);
  useEffect(() => { workDirRef.current = state.workDir; }, [state.workDir]);
  useEffect(() => { fileTreeRef.current = state.fileTree; }, [state.fileTree]);

  // ── Derived conversation state ────────────────────────────────────────────
  const activeTurn = state.turns.length ? state.turns[state.turns.length - 1] : null;
  const phase: AgentPhase = activeTurn ? activeTurn.phase : 'idle';
  const isBusy = activeTurn ? BUSY_PHASES.includes(activeTurn.phase) : false;
  // Model chosen for the active session (null = .env default). Persisted per session.
  const selectedModel = sessionModels[state.currentSessionId] ?? null;
  // Plan-review toggle for the active session (default off). Persisted per session.
  const planReviewEnabled = sessionPlanReview[state.currentSessionId] ?? false;
  // Composer mode for the active session; default "planning" matches the prior
  // composer default. Absence in the map means never toggled → "planning".
  const sessionModeValue: 'planning' | 'fast' = sessionMode[state.currentSessionId] ?? 'planning';
  const reviewTurn = state.reviewTurnId ? state.turns.find(t => t.id === state.reviewTurnId) ?? null : null;
  const pendingPatchCount = useMemo(() => state.turns.reduce((n, t) => n + t.patches.length, 0), [state.turns]);
  // Sessions for the dropdown: active first, then any session we have in-run turns
  // for (so a just-used local session stays selectable even before the daemon list
  // catches up — and in mock, where list_sessions is static), then the daemon's
  // list. "default" is hidden. A Set keeps insertion order and de-dupes.
  const displayedSessions = useMemo(() => {
    const set = new Set<string>();
    if (state.currentSessionId) set.add(state.currentSessionId);
    for (const id of Object.keys(state.archivedTurns)) set.add(id);
    for (const s of state.sessions) if (s !== 'default') set.add(s);
    return [...set];
  }, [state.sessions, state.currentSessionId, state.archivedTurns]);

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
      if (msg.type === 'done') {
        // A completed task may have just persisted this session's .db — refresh the list.
        ipc.listSessions().then(s => dispatch({ type: 'SET_SESSIONS', sessions: s })).catch(() => {});
        // …and it may have created/edited/deleted files — re-read the open folder
        // so the explorer reflects disk without a manual refresh. Only when a
        // folder is open; errors (e.g. the folder was removed) are swallowed so a
        // failed re-read just leaves the current tree intact.
        const dir = workDirRef.current;
        if (dir) {
          refreshTreePreservingExpanded(dir, fileTreeRef.current)
            .then(tree => dispatch({ type: 'SET_FILE_TREE', tree }))
            .catch(() => { /* folder unreadable/removed — keep the current tree */ });
        }
      }
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
      // Thread the active session id so the daemon resumes/persists this session
      // (persist=True + per-session agent cache => the agent remembers prior turns).
      // activeSkills: null → omit param (daemon loads all). Set → send array.
      const skillsArg = activeSkills && activeSkills.size > 0 ? [...activeSkills] : null;
      // model: the session's chosen model id, or null = the daemon's .env default.
      const modelArg = sessionModels[state.currentSessionId] || null;
      // plan_review: the session toggle (default false) — pause after planning.
      const planReviewArg = sessionPlanReview[state.currentSessionId] ?? false;
      await ipc.runTask(task, mode, false, state.currentSessionId, state.workDir || undefined, skillsArg, modelArg, planReviewArg);
    } catch (err) {
      const m = err instanceof Error ? err.message : String(err);
      if (/already running/i.test(m)) {
        // Single-flight (-32000): NOT a task failure. Drop the empty turn and show a
        // non-destructive notice; the in-flight turn keeps streaming untouched.
        dispatch({ type: 'REMOVE_TURN', id: turn.id });
        setNotice('A task is already running — wait for it to finish.');
      } else {
        // A real submit failure surfaces as the turn's single error (no chat-line spam).
        dispatch({ type: 'AGENT_MSG', msg: { type: 'error', payload: { message: m || 'Failed to submit task', suggestion: '' } } });
      }
    }
    // state.workDir MUST be in deps: switching folders mid-session changes
    // workDir but NOT currentSessionId, so without it this callback would close
    // over the stale (previous) workDir and keep sending it — the daemon then
    // sees the same work_dir for the thread_id and never evicts/rebuilds the
    // cached agent, so the agent keeps working in the old folder.
  }, [state.currentSessionId, state.workDir, activeSkills, sessionModels, sessionPlanReview]);

  // Pick a model for the active session (or null = revert to the .env default).
  // Stored per thread_id; the choice is sent on the next run_task and the daemon
  // evicts/rebuilds the cached agent so that task uses the new model.
  const handleSelectModel = useCallback((id: string | null) => {
    setSessionModels(prev => {
      const next = { ...prev };
      if (id) next[state.currentSessionId] = id;
      else delete next[state.currentSessionId];
      return next;
    });
  }, [state.currentSessionId]);

  // Plan review (HITL) per-session toggle. Off = today's run-through. Locked
  // while a task runs (the header disables it) so it can't flip mid-run.
  const handleTogglePlanReview = useCallback((on: boolean) => {
    setSessionPlanReview(prev => {
      const next = { ...prev };
      if (on) next[state.currentSessionId] = true;
      else delete next[state.currentSessionId];
      return next;
    });
  }, [state.currentSessionId]);

  const handleSetMode = useCallback((mode: 'planning' | 'fast') => {
    setSessionMode(prev => ({ ...prev, [state.currentSessionId]: mode }));
  }, [state.currentSessionId]);

  // Accept (continue to execute) / reject (stop) a paused plan. Optimistically
  // hide the buttons, then resume the daemon; the daemon's events (execution_started
  // or plan_rejected) drive the authoritative turn state.
  const handlePlanDecision = useCallback(async (turnId: string, accept: boolean) => {
    dispatch({ type: 'PLAN_REVIEWED', turnId });
    try { await ipc.resumePlan(accept); } catch { /* best-effort */ }
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

  const handleNewSession = useCallback(() => {
    dispatch({ type: 'NEW_SESSION', id: newSessionId() });   // archives current, empty transcript
    setShowDiffReview(false);
    ipc.listSessions().then(s => dispatch({ type: 'SET_SESSIONS', sessions: s })).catch(() => {});
  }, []);

  const handleSwitchSession = useCallback((id: string) => {
    dispatch({ type: 'SWITCH_SESSION', id });
    setShowDiffReview(false);
  }, []);

  // Rename a session's display label only — the thread_id is unchanged, so the
  // daemon still resumes the same memory. An empty/blank name clears the custom
  // label (reverts the dropdown to the thread_id fallback).
  const handleRenameSession = useCallback((id: string, name: string) => {
    const trimmed = name.trim();
    setSessionNames(prev => {
      if (!trimmed) {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      }
      if (prev[id] === trimmed) return prev;
      return { ...prev, [id]: trimmed };
    });
  }, []);

  // ── Command registry ──────────────────────────────────────────────────────

  const commands: Command[] = useMemo(() => [
    { id: 'open-folder',       label: 'Open Folder',          category: 'File',         shortcut: 'Ctrl+O', action: handleSelectFolder },
    { id: 'toggle-explorer',   label: 'Toggle Explorer',      category: 'View',         shortcut: 'Ctrl+B', action: () => setExplorerCollapsed(p => !p) },
    { id: 'toggle-agent',      label: 'Toggle Agent Panel',   category: 'View',         shortcut: 'Ctrl+J', action: () => setAgentCollapsed(p => !p) },
    { id: 'open-settings',     label: 'Open Settings',        category: 'Preferences',  shortcut: 'Ctrl+,', action: () => setShowSettings(true) },
    { id: 'new-task',          label: 'New Task',             category: 'Agent',        shortcut: 'Ctrl+K', action: () => { setAgentCollapsed(false); setFocus('agent'); } },
    { id: 'new-session',       label: 'New Session',          category: 'Agent',        action: handleNewSession },
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
  ], [handleSelectFolder, handleReviewDiffs, handleAbort, handleNewSession, state.turns, activeTurn]);

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
                    activeSkills={activeSkills}
                    onToggleSkill={(name, allNames) => {
                      setActiveSkills(prev => {
                        // null = all selected; convert to full set, then toggle.
                        const current = prev ?? new Set(allNames);
                        const next = new Set(current);
                        if (next.has(name)) next.delete(name); else next.add(name);
                        // If all are now checked, collapse back to null (= "all").
                        return next.size === allNames.length ? null : next;
                      });
                    }}
                    onSelectAll={() => setActiveSkills(null)}
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
                sessions={displayedSessions}
                currentSessionId={state.currentSessionId}
                sessionNames={sessionNames}
                onSwitchSession={handleSwitchSession}
                onNewSession={handleNewSession}
                onRenameSession={handleRenameSession}
                notice={notice}
                onDismissNotice={() => setNotice(null)}
                onSubmitTask={handleSubmitTask}
                onReviewDiffs={handleReviewDiffs}
                onFileDecision={handleFileDecision}
                onDismissError={(turnId) => dispatch({ type: 'CLEAR_TURN_ERROR', turnId })}
                onSettingsClick={() => setShowSettings(true)}
                onCollapseClick={() => setAgentCollapsed(true)}
                activeSkill={activeSkill}
                onDismissSkill={() => setActiveSkill(null)}
                onAbort={handleAbort}
                models={models}
                modelsLoading={modelsLoading}
                selectedModel={selectedModel}
                onSelectModel={handleSelectModel}
                planReview={planReviewEnabled}
                onTogglePlanReview={handleTogglePlanReview}
                mode={sessionModeValue}
                onModeChange={handleSetMode}
                onPlanDecision={handlePlanDecision}
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

/** Find an entry by absolute path anywhere in the (possibly nested) tree. */
function findEntryByPath(tree: FileEntry[], path: string): FileEntry | null {
  for (const entry of tree) {
    if (entry.path === path) return entry;
    if (entry.children) {
      const found = findEntryByPath(entry.children, path);
      if (found) return found;
    }
  }
  return null;
}

/**
 * Re-read `dir` from disk and return a fresh tree that preserves the previous
 * tree's expansion state: any directory whose children were already loaded is
 * re-fetched recursively, so files the agent created/edited/deleted show up at
 * every currently-visible level — not just the root. Directories that were
 * never expanded stay unloaded (children undefined), exactly as the lazy-load
 * path leaves them. A vanished directory (deleted mid-task) is left unloaded
 * rather than aborting the whole refresh.
 */
async function refreshTreePreservingExpanded(dir: string, prevTree: FileEntry[]): Promise<FileEntry[]> {
  const fresh = await ipc.listDirectory(dir);
  for (const entry of fresh) {
    if (!entry.isDirectory) continue;
    const prev = findEntryByPath(prevTree, entry.path);
    if (prev && Array.isArray(prev.children)) {
      try {
        entry.children = await refreshTreePreservingExpanded(entry.path, prev.children);
      } catch {
        // Directory removed during the task — leave it collapsed/unloaded.
      }
    }
  }
  return fresh;
}
