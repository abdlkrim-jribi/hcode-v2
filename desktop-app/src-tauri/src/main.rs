//! HCode v2 Desktop — Tauri main process.
//!
//! v2 additions vs v1:
//!   list_skills, list_workflows, run_workflow
//!   list_mcp_servers, connect_mcp_server, disconnect_mcp_server
//!
//! NOTE: These query commands send a JSON-RPC request and return immediately.
//! The response arrives as a `daemon-message` Tauri event that the bridge.ts
//! WS handler correlates by `id`.  For a fully synchronous Tauri command,
//! a oneshot-channel correlator should be added to DaemonSupervisor.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod daemon;

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::{AppHandle, State};

struct AppState {
    daemon:   Mutex<daemon::DaemonSupervisor>,
    work_dir: Mutex<Option<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonInfo {
    pub status:  String,
    pub uptime:  Option<u64>,
    pub pid:     Option<u32>,
    pub version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileEntry {
    pub name:        String,
    pub path:        String,
    #[serde(rename = "isDirectory")]
    pub is_directory: bool,
    pub children:    Option<Vec<FileEntry>>,
}

// ── Daemon lifecycle ──────────────────────────────────────────────────────────

#[tauri::command]
async fn start_daemon(state: State<'_, AppState>, app: AppHandle) -> Result<DaemonInfo, String> {
    let mut s = state.daemon.lock().map_err(|e| e.to_string())?;
    s.start(&app).map_err(|e| e.to_string())?;
    Ok(DaemonInfo { status: "running".to_string(), uptime: Some(0), pid: s.pid(), version: None })
}

#[tauri::command]
async fn stop_daemon(state: State<'_, AppState>) -> Result<(), String> {
    state.daemon.lock().map_err(|e| e.to_string())?.stop().map_err(|e| e.to_string())
}

#[tauri::command]
async fn daemon_health(state: State<'_, AppState>) -> Result<DaemonInfo, String> {
    let s = state.daemon.lock().map_err(|e| e.to_string())?;
    Ok(DaemonInfo { status: s.status().to_string(), uptime: s.uptime(), pid: s.pid(), version: None })
}

// ── Task commands ─────────────────────────────────────────────────────────────

#[tauri::command]
async fn run_task(task: String, mode: String, autonomous: bool, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "run_task", serde_json::json!({ "task": task, "mode": mode, "autonomous": autonomous }))
}
#[tauri::command]
async fn abort_task(state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "abort", serde_json::json!({}))
}
#[tauri::command]
async fn approve_plan(state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "approve_plan", serde_json::json!({}))
}
#[tauri::command]
async fn reject_plan(feedback: String, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "reject_plan", serde_json::json!({ "feedback": feedback }))
}
#[tauri::command]
async fn accept_patch(path: String, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "accept_patch", serde_json::json!({ "path": path }))
}
#[tauri::command]
async fn reject_patch(path: String, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "reject_patch", serde_json::json!({ "path": path }))
}
#[tauri::command]
async fn rollback_all(state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "rollback_all", serde_json::json!({}))
}

// ── v2 daemon methods ─────────────────────────────────────────────────────────

#[tauri::command]
async fn list_skills(state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "list_skills", serde_json::json!({}))
}
#[tauri::command]
async fn list_workflows(state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "list_workflows", serde_json::json!({}))
}
#[tauri::command]
async fn run_workflow(workflow: String, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "run_workflow", serde_json::json!({ "workflow": workflow }))
}
#[tauri::command]
async fn list_mcp_servers(state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "list_mcp_servers", serde_json::json!({}))
}
#[tauri::command]
async fn connect_mcp_server(server: String, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "connect_mcp_server", serde_json::json!({ "server": server }))
}
#[tauri::command]
async fn disconnect_mcp_server(server: String, state: State<'_, AppState>) -> Result<(), String> {
    rpc(&state, "disconnect_mcp_server", serde_json::json!({ "server": server }))
}

// ── File system commands ──────────────────────────────────────────────────────

#[tauri::command]
async fn open_folder_dialog(app: AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().set_title("Open Folder").pick_folder(move |p| {
        let _ = tx.send(p.map(|pp| pp.to_string()));
    });
    rx.recv().map_err(|e| e.to_string())
}

#[tauri::command]
async fn list_directory(path: String) -> Result<Vec<FileEntry>, String> {
    let dir = PathBuf::from(&path);
    if !dir.is_dir() { return Err(format!("Not a directory: {}", path)); }
    let mut entries = Vec::new();
    for e in std::fs::read_dir(&dir).map_err(|e| e.to_string())? {
        let e = e.map_err(|e| e.to_string())?;
        let name = e.file_name().to_string_lossy().to_string();
        if name.starts_with('.') || matches!(name.as_str(), "node_modules" | "__pycache__" | "target") {
            continue;
        }
        let meta = e.metadata().map_err(|e| e.to_string())?;
        entries.push(FileEntry { name, path: e.path().to_string_lossy().to_string(), is_directory: meta.is_dir(), children: None });
    }
    entries.sort_by(|a, b| b.is_directory.cmp(&a.is_directory).then(a.name.to_lowercase().cmp(&b.name.to_lowercase())));
    Ok(entries)
}

#[tauri::command]
async fn read_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path).map_err(|e| format!("read {}: {}", path, e))
}
#[tauri::command]
async fn write_file(path: String, content: String) -> Result<(), String> {
    std::fs::write(&path, &content).map_err(|e| format!("write {}: {}", path, e))
}

// ── Secure storage ────────────────────────────────────────────────────────────

#[tauri::command]
async fn save_api_key(provider: String, key: String) -> Result<(), String> {
    keyring::Entry::new("hcode-v2-desktop", &provider).map_err(|e| e.to_string())?.set_password(&key).map_err(|e| e.to_string())
}
#[tauri::command]
async fn get_api_key(provider: String) -> Result<Option<String>, String> {
    match keyring::Entry::new("hcode-v2-desktop", &provider).map_err(|e| e.to_string())?.get_password() {
        Ok(k)                      => Ok(Some(k)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e)                     => Err(e.to_string()),
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn rpc(state: &State<'_, AppState>, method: &str, params: serde_json::Value) -> Result<(), String> {
    use std::time::{SystemTime, UNIX_EPOCH};
    let id = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().subsec_nanos().to_string();
    let msg = serde_json::json!({ "jsonrpc": "2.0", "id": id, "method": method, "params": params });
    state.daemon.lock().map_err(|e| e.to_string())?.send(&msg.to_string()).map_err(|e| e.to_string())
}

// ── Main ──────────────────────────────────────────────────────────────────────

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            use tauri::Manager;
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize(); let _ = w.show(); let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .manage(AppState { daemon: Mutex::new(daemon::DaemonSupervisor::new()), work_dir: Mutex::new(None) })
        .invoke_handler(tauri::generate_handler![
            start_daemon, stop_daemon, daemon_health,
            run_task, abort_task, approve_plan, reject_plan,
            accept_patch, reject_patch, rollback_all,
            list_skills, list_workflows, run_workflow,
            list_mcp_servers, connect_mcp_server, disconnect_mcp_server,
            open_folder_dialog, list_directory, read_file, write_file,
            save_api_key, get_api_key,
        ])
        .run(tauri::generate_context!())
        .expect("error running HCode v2 Desktop");
}
