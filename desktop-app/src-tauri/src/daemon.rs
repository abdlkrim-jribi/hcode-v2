//! Daemon Supervisor — manages the HCode v2 Python daemon process.
//!
//! v2 change vs v1: spawns `python -m hcode_v2.daemon` (via uv or python)
//! instead of v1's `python hcode-daemon/run_daemon.py`.
//!
//! Discovery order:
//!   1. Bundled binary  hcode-v2-daemon[.exe] next to the executable
//!   2. `uv run python -m hcode_v2.daemon`  (uv on PATH)
//!   3. `python -m hcode_v2.daemon`          (python on PATH)
//!
//! Communication: JSON-RPC 2.0 over stdin/stdout (same as v1).
//! Response routing: stdout reader emits every JSON line as a Tauri
//! `daemon-message` event.  For query commands (list_skills etc.) the
//! frontend correlates responses by `id` field.
//!
//! Failure visibility (fix/daemon-failure-visibility)
//! --------------------------------------------------
//! A spawned child that dies ~immediately (e.g. `uv run` outside the source
//! tree, no pyproject to resolve) USED to leave status stuck at "running"
//! forever with no error surfaced — a dead brain with a green pill. Now:
//!   * status lives in a shared `AtomicU8` the reader/watchdog threads update;
//!   * we report "starting" on spawn and flip to "running" ONLY when the stdout
//!     reader sees the daemon's `{"type":"ready"}` line (server.py emits it first);
//!   * if the child exits before ready, OR no ready arrives within 15s, status
//!     becomes "error" and a `daemon-error` event carries {message, tried,
//!     stderr_tail} so the UI can show WHY;
//!   * stderr is drained into a rolling 40-line tail (never was read before).
//! Secrets never touch these payloads — only process stderr + the discovery
//! tried-list, neither of which carries tokens.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

// Status as an atomic so reader/watchdog threads and the command handlers all
// see one authoritative value (the old `DaemonStatus` field was thread-local to
// the supervisor and could never be corrected by a reader thread on child exit).
const S_STOPPED: u8 = 0;
const S_STARTING: u8 = 1;
const S_RUNNING: u8 = 2;
const S_ERROR: u8 = 3;

/// How long to wait for the daemon's `ready` line before declaring failure.
const READY_TIMEOUT: Duration = Duration::from_secs(15);
/// Rolling stderr tail length kept for the `daemon-error` payload.
const STDERR_TAIL_MAX: usize = 40;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum DaemonStatus { Stopped, Starting, Running, Error }

impl DaemonStatus {
    fn from_u8(v: u8) -> Self {
        match v {
            S_STARTING => DaemonStatus::Starting,
            S_RUNNING  => DaemonStatus::Running,
            S_ERROR    => DaemonStatus::Error,
            _          => DaemonStatus::Stopped,
        }
    }
}

impl std::fmt::Display for DaemonStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DaemonStatus::Stopped  => write!(f, "stopped"),
            DaemonStatus::Starting => write!(f, "starting"),
            DaemonStatus::Running  => write!(f, "running"),
            DaemonStatus::Error    => write!(f, "error"),
        }
    }
}

/// Where daemon events go. Abstracted so the supervision core can be unit-tested
/// without a live Tauri `AppHandle` (tests inject a recording sink).
pub trait EventSink: Send + Sync + 'static {
    fn emit_event(&self, event: &str, payload: serde_json::Value);
}

struct AppSink(AppHandle);
impl EventSink for AppSink {
    fn emit_event(&self, event: &str, payload: serde_json::Value) {
        let _ = self.0.emit(event, payload);
    }
}

/// Append a line to the rolling tail, evicting the oldest past the cap.
fn push_tail(tail: &Arc<Mutex<VecDeque<String>>>, line: String) {
    if let Ok(mut t) = tail.lock() {
        t.push_back(line);
        while t.len() > STDERR_TAIL_MAX {
            t.pop_front();
        }
    }
}

fn collect_tail(tail: &Arc<Mutex<VecDeque<String>>>) -> Vec<String> {
    tail.lock().map(|t| t.iter().cloned().collect()).unwrap_or_default()
}

pub struct DaemonSupervisor {
    process:       Option<Child>,
    status:        Arc<AtomicU8>,
    stderr_tail:   Arc<Mutex<VecDeque<String>>>,
    start_time:    Option<Instant>,
    restart_count: u32,
}

impl DaemonSupervisor {
    pub fn new() -> Self {
        Self {
            process: None,
            status: Arc::new(AtomicU8::new(S_STOPPED)),
            stderr_tail: Arc::new(Mutex::new(VecDeque::new())),
            start_time: None,
            restart_count: 0,
        }
    }

    pub fn start(&mut self, app: &AppHandle) -> Result<(), String> {
        // Guard BOTH running and starting: the old code flipped to Running
        // synchronously, so a duplicate start() (e.g. React StrictMode mounting
        // the daemon effect twice in dev) hit this guard. Now that spawn only
        // reports Starting, a second call must be turned away here too — otherwise
        // it would spawn a second daemon and overwrite self.process, orphaning the
        // first child and racing its reader thread against the ready-gate.
        let code = self.status_code();
        if code == S_RUNNING || code == S_STARTING { return Ok(()); }

        let (found, tried) = self.find_daemon_command(app);
        let sink: Arc<dyn EventSink> = Arc::new(AppSink(app.clone()));

        let (program, args) = match found {
            Some(pa) => pa,
            None => {
                // Item 4: nothing on disk to run — route the tried-list through
                // the SAME daemon-error channel, not just a returned Err string.
                let msg = format!(
                    "Cannot locate the v2 daemon.\nTried (in order):\n  - {}\n\n\
                     Install uv (https://docs.astral.sh/uv/) and run `uv sync` in \
                     hcode-v2/, or ensure `python -m hcode_v2.daemon` works.",
                    tried.join("\n  - ")
                );
                self.status.store(S_ERROR, Ordering::SeqCst);
                sink.emit_event("daemon-error", serde_json::json!({
                    "message": msg, "tried": tried, "stderr_tail": Vec::<String>::new(),
                }));
                return Err(msg);
            }
        };
        self.launch(program, args, tried, sink)
    }

    /// Spawn the child and wire up the reader + watchdog threads. Split out from
    /// `start` (which resolves the command via Tauri) so tests can drive it with
    /// a fake command and a recording sink.
    fn launch(
        &mut self,
        program: String,
        args: Vec<String>,
        tried: Vec<String>,
        sink: Arc<dyn EventSink>,
    ) -> Result<(), String> {
        // Item 3: spawn success is NOT readiness. Report "starting" and let the
        // ready line (or its absence) decide.
        self.status.store(S_STARTING, Ordering::SeqCst);
        if let Ok(mut t) = self.stderr_tail.lock() { t.clear(); }

        let mut cmd = Command::new(&program);
        for a in &args { cmd.arg(a); }

        let mut child = cmd
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| {
                let msg = format!("Failed to start `{} {}`: {}", program, args.join(" "), e);
                self.status.store(S_ERROR, Ordering::SeqCst);
                sink.emit_event("daemon-error", serde_json::json!({
                    "message": msg, "tried": tried.clone(), "stderr_tail": Vec::<String>::new(),
                }));
                msg
            })?;

        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        self.process       = Some(child);
        self.start_time    = Some(Instant::now());
        self.restart_count = 0;

        let tried = Arc::new(tried);

        // Item 2: drain stderr into the rolling tail (previously never read, so
        // the daemon's own error message vanished).
        if let Some(err) = stderr {
            let tail = self.stderr_tail.clone();
            std::thread::spawn(move || {
                for line in BufReader::new(err).lines().map_while(Result::ok) {
                    let t = line.trim().to_string();
                    if t.is_empty() { continue; }
                    eprintln!("[Daemon stderr] {}", t);
                    push_tail(&tail, t);
                }
            });
        }

        // stdout reader: message pump + ready-gate + exit handling.
        if let Some(out) = stdout {
            let status = self.status.clone();
            let tail   = self.stderr_tail.clone();
            let tried  = tried.clone();
            let sink   = sink.clone();
            std::thread::spawn(move || {
                let reader = BufReader::new(out);
                for line in reader.lines() {
                    match line {
                        Ok(text) => {
                            let t = text.trim();
                            if t.is_empty() { continue; }
                            let json = serde_json::from_str::<serde_json::Value>(t)
                                .unwrap_or_else(|_| serde_json::json!({
                                    "type": "log",
                                    "payload": { "line": t, "stream": "stdout" }
                                }));
                            // Ready-gate: first `ready` flips starting -> running.
                            // compare_exchange so a late watchdog/error can't be
                            // clobbered, and so we only transition from starting.
                            if json.get("type").and_then(|v| v.as_str()) == Some("ready") {
                                let _ = status.compare_exchange(
                                    S_STARTING, S_RUNNING, Ordering::SeqCst, Ordering::SeqCst);
                            }
                            sink.emit_event("daemon-message", json);
                        }
                        Err(e) => { eprintln!("[Daemon] read error: {}", e); break; }
                    }
                }
                // stdout closed => the child exited (or the pipe broke).
                if status
                    .compare_exchange(S_STARTING, S_ERROR, Ordering::SeqCst, Ordering::SeqCst)
                    .is_ok()
                {
                    // Died BEFORE ready — the silent-failure case this fix targets.
                    sink.emit_event("daemon-error", serde_json::json!({
                        "message": "The daemon process exited before it became ready.",
                        "tried": &*tried,
                        "stderr_tail": collect_tail(&tail),
                    }));
                } else if status.load(Ordering::SeqCst) == S_RUNNING {
                    // Was healthy, then exited — the pre-existing "stopped" path.
                    status.store(S_STOPPED, Ordering::SeqCst);
                    sink.emit_event("daemon-status", serde_json::json!({ "status": "stopped" }));
                }
                // Stopped (user stop) / Error (watchdog won) => nothing to emit.
                eprintln!("[Daemon] reader exited");
            });
        }

        // Watchdog: if no ready within the timeout, declare error once. Loses the
        // race harmlessly if the stdout-EOF handler already set error/stopped.
        {
            let status = self.status.clone();
            let tail   = self.stderr_tail.clone();
            let tried  = tried.clone();
            let sink   = sink.clone();
            std::thread::spawn(move || {
                std::thread::sleep(READY_TIMEOUT);
                if status
                    .compare_exchange(S_STARTING, S_ERROR, Ordering::SeqCst, Ordering::SeqCst)
                    .is_ok()
                {
                    sink.emit_event("daemon-error", serde_json::json!({
                        "message": format!(
                            "The daemon did not become ready within {}s.",
                            READY_TIMEOUT.as_secs()),
                        "tried": &*tried,
                        "stderr_tail": collect_tail(&tail),
                    }));
                }
            });
        }

        Ok(())
    }

    pub fn stop(&mut self) -> Result<(), String> {
        // Set Stopped BEFORE killing so the stdout-EOF handler sees an intentional
        // stop (neither error nor a spurious "stopped" event).
        self.status.store(S_STOPPED, Ordering::SeqCst);
        if let Some(ref mut child) = self.process {
            if let Some(ref mut stdin) = child.stdin {
                use std::io::Write;
                let _ = writeln!(stdin, r#"{{"jsonrpc":"2.0","method":"shutdown"}}"#);
            }
            std::thread::sleep(std::time::Duration::from_millis(400));
            let _ = child.kill();
            let _ = child.wait();
        }
        self.process    = None;
        self.start_time = None;
        Ok(())
    }

    pub fn send(&mut self, message: &str) -> Result<(), String> {
        // Allow Starting as well as Running: writes buffer in the pipe and the
        // daemon reads them once its stdin loop begins (just after `ready`), so
        // mount-time queries fired during startup are not lost.
        let code = self.status_code();
        if code != S_RUNNING && code != S_STARTING {
            return Err("Daemon is not running".to_string());
        }
        if let Some(ref mut child) = self.process {
            if let Some(ref mut stdin) = child.stdin {
                use std::io::Write;
                writeln!(stdin, "{}", message)
                    .map_err(|e| format!("stdin write failed: {}", e))?;
                stdin.flush()
                    .map_err(|e| format!("stdin flush failed: {}", e))?;
                return Ok(());
            }
        }
        Err("Daemon stdin unavailable".to_string())
    }

    fn status_code(&self) -> u8 { self.status.load(Ordering::SeqCst) }
    pub fn status(&self)  -> DaemonStatus { DaemonStatus::from_u8(self.status_code()) }
    pub fn pid(&self)     -> Option<u32>   { self.process.as_ref().map(|p| p.id()) }
    pub fn uptime(&self)  -> Option<u64>   { self.start_time.map(|t| t.elapsed().as_secs()) }

    // ── Discovery ─────────────────────────────────────────────────────────────

    /// Resolve the daemon command. Returns `(Some((program, args)), tried)` or
    /// `(None, tried)` when nothing on disk can run it. Discovery order is
    /// UNCHANGED: bundled binary → uv → python.
    fn find_daemon_command(&self, app: &AppHandle) -> (Option<(String, Vec<String>)>, Vec<String>) {
        let mut tried: Vec<String> = Vec::new();

        // 1. Bundled binary
        if let Ok(dir) = app.path().resource_dir() {
            for name in &["hcode-v2-daemon.exe", "hcode-v2-daemon"] {
                let p = dir.join(name);
                tried.push(format!("bundled binary: {}", p.display()));
                if p.exists() {
                    return (Some((p.to_string_lossy().to_string(), Vec::new())), tried);
                }
            }
        }

        // 2. uv run python -m hcode_v2.daemon  (preferred for dev — uses the venv)
        if which_exists("uv") {
            tried.push("uv run python -m hcode_v2.daemon".to_string());
            return (
                Some(("uv".to_string(),
                     vec!["run".to_string(), "python".to_string(),
                          "-m".to_string(), "hcode_v2.daemon".to_string()])),
                tried,
            );
        }

        // 3. python -m hcode_v2.daemon  (fallback)
        for py in &["python3", "python"] {
            if which_exists(py) {
                tried.push(format!("{} -m hcode_v2.daemon", py));
                return (
                    Some((py.to_string(),
                         vec!["-m".to_string(), "hcode_v2.daemon".to_string()])),
                    tried,
                );
            }
        }

        (None, tried)
    }
}

fn which_exists(name: &str) -> bool {
    #[cfg(target_os = "windows")]
    { Command::new("where").arg(name).output().map(|o| o.status.success()).unwrap_or(false) }
    #[cfg(not(target_os = "windows"))]
    { Command::new("which").arg(name).output().map(|o| o.status.success()).unwrap_or(false) }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct RecordingSink(Arc<Mutex<Vec<(String, serde_json::Value)>>>);
    impl EventSink for RecordingSink {
        fn emit_event(&self, event: &str, payload: serde_json::Value) {
            self.0.lock().unwrap().push((event.to_string(), payload));
        }
    }

    // Pure logic: the rolling tail keeps only the last STDERR_TAIL_MAX lines.
    #[test]
    fn stderr_tail_caps_and_keeps_latest() {
        let tail: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
        for i in 0..(STDERR_TAIL_MAX + 20) {
            push_tail(&tail, format!("line{}", i));
        }
        let out = collect_tail(&tail);
        assert_eq!(out.len(), STDERR_TAIL_MAX);
        assert_eq!(out.first().unwrap(), &format!("line{}", 20)); // oldest evicted
        assert_eq!(out.last().unwrap(),  &format!("line{}", STDERR_TAIL_MAX + 19));
    }

    // A fake "daemon" that writes to stderr then exits WITHOUT ever emitting
    // {"type":"ready"} must land in Error, capture the stderr, and fire a
    // daemon-error event — the exact silent-failure scenario this fix targets.
    #[cfg(target_os = "windows")]
    #[test]
    fn dead_daemon_before_ready_errors_and_captures_stderr() {
        let mut sup = DaemonSupervisor::new();
        let events = Arc::new(Mutex::new(Vec::new()));
        let sink: Arc<dyn EventSink> = Arc::new(RecordingSink(events.clone()));

        sup.launch(
            "cmd".to_string(),
            vec!["/C".to_string(), "echo boom 1>&2& exit 1".to_string()],
            vec!["fake: cmd /C (echo boom; exit 1)".to_string()],
            sink,
        ).expect("launch should spawn");

        // Poll (child dies almost immediately; give the threads time to run).
        let mut became_error = false;
        for _ in 0..60 {
            std::thread::sleep(Duration::from_millis(50));
            if sup.status() == DaemonStatus::Error { became_error = true; break; }
        }
        assert!(became_error, "status never became Error, got {:?}", sup.status());

        // Give the stderr reader a beat to flush its line into the tail.
        for _ in 0..20 {
            if !collect_tail(&sup.stderr_tail).is_empty() { break; }
            std::thread::sleep(Duration::from_millis(50));
        }
        let tail = collect_tail(&sup.stderr_tail);
        assert!(tail.iter().any(|l| l.contains("boom")), "stderr tail missing 'boom': {:?}", tail);

        let evs = events.lock().unwrap();
        assert!(
            evs.iter().any(|(e, _)| e == "daemon-error"),
            "expected a daemon-error event, got: {:?}",
            evs.iter().map(|(e, _)| e).collect::<Vec<_>>()
        );
    }
}
