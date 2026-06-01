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

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::time::Instant;
use tauri::{AppHandle, Emitter, Manager};

pub struct DaemonSupervisor {
    process:       Option<Child>,
    status:        DaemonStatus,
    start_time:    Option<Instant>,
    restart_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum DaemonStatus { Stopped, Starting, Running, Error }

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

impl DaemonSupervisor {
    pub fn new() -> Self {
        Self { process: None, status: DaemonStatus::Stopped, start_time: None, restart_count: 0 }
    }

    pub fn start(&mut self, app: &AppHandle) -> Result<(), String> {
        if self.status == DaemonStatus::Running { return Ok(()); }
        self.status = DaemonStatus::Starting;

        let (program, args) = self.find_daemon_command(app).map_err(|e| {
            self.status = DaemonStatus::Error;
            e
        })?;

        let mut cmd = Command::new(&program);
        for a in &args { cmd.arg(a); }

        let child = cmd
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| {
                self.status = DaemonStatus::Error;
                format!("Failed to start `{} {}`: {}", program, args.join(" "), e)
            })?;

        self.process      = Some(child);
        self.status       = DaemonStatus::Running;
        self.start_time   = Some(Instant::now());
        self.restart_count = 0;
        self.spawn_output_reader(app.clone());
        Ok(())
    }

    pub fn stop(&mut self) -> Result<(), String> {
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
        self.status     = DaemonStatus::Stopped;
        self.start_time = None;
        Ok(())
    }

    pub fn send(&mut self, message: &str) -> Result<(), String> {
        if self.status != DaemonStatus::Running {
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

    pub fn status(&self)  -> &DaemonStatus { &self.status }
    pub fn pid(&self)     -> Option<u32>   { self.process.as_ref().map(|p| p.id()) }
    pub fn uptime(&self)  -> Option<u64>   { self.start_time.map(|t| t.elapsed().as_secs()) }

    // ── Discovery ─────────────────────────────────────────────────────────────

    fn find_daemon_command(&self, app: &AppHandle) -> Result<(String, Vec<String>), String> {
        let mut tried: Vec<String> = Vec::new();

        // 1. Bundled binary
        if let Ok(dir) = app.path().resource_dir() {
            for name in &["hcode-v2-daemon.exe", "hcode-v2-daemon"] {
                let p = dir.join(name);
                tried.push(format!("bundled binary: {}", p.display()));
                if p.exists() { return Ok((p.to_string_lossy().to_string(), Vec::new())); }
            }
        }

        // 2. uv run python -m hcode_v2.daemon  (preferred for dev — uses the venv)
        if which_exists("uv") {
            tried.push("uv run python -m hcode_v2.daemon".to_string());
            return Ok((
                "uv".to_string(),
                vec!["run".to_string(), "python".to_string(),
                     "-m".to_string(), "hcode_v2.daemon".to_string()],
            ));
        }

        // 3. python -m hcode_v2.daemon  (fallback)
        for py in &["python3", "python"] {
            if which_exists(py) {
                tried.push(format!("{} -m hcode_v2.daemon", py));
                return Ok((
                    py.to_string(),
                    vec!["-m".to_string(), "hcode_v2.daemon".to_string()],
                ));
            }
        }

        Err(format!(
            "Cannot locate the v2 daemon.\n\
             Tried (in order):\n  - {}\n\n\
             Install uv (https://docs.astral.sh/uv/) and run `uv sync` in hcode-v2/, \
             or ensure `python -m hcode_v2.daemon` works from this directory.",
            tried.join("\n  - ")
        ))
    }

    fn spawn_output_reader(&mut self, app: AppHandle) {
        let stdout = self.process.as_mut().and_then(|c| c.stdout.take());
        std::thread::spawn(move || {
            if let Some(out) = stdout {
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
                            let _ = app.emit("daemon-message", json);
                        }
                        Err(e) => { eprintln!("[Daemon] read error: {}", e); break; }
                    }
                }
            }
            let _ = app.emit("daemon-status", serde_json::json!({ "status": "stopped" }));
            eprintln!("[Daemon] reader exited");
        });
    }
}

fn which_exists(name: &str) -> bool {
    #[cfg(target_os = "windows")]
    { Command::new("where").arg(name).output().map(|o| o.status.success()).unwrap_or(false) }
    #[cfg(not(target_os = "windows"))]
    { Command::new("which").arg(name).output().map(|o| o.status.success()).unwrap_or(false) }
}
