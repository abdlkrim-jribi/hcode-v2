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
    daemon: Mutex<daemon::DaemonSupervisor>,
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

// rename_all = "snake_case" is MANDATORY: Tauri v2 defaults to camelCase invoke
// args, but bridge.ts sends snake_case keys (it must — server.py reads the same
// payload verbatim on the WS path). Without it, work_dir/thread_id bind to None
// and the agent silently runs in the daemon's cwd instead of the opened folder.
#[tauri::command(rename_all = "snake_case")]
async fn run_task(
    task: String, mode: String, autonomous: bool,
    thread_id: Option<String>, work_dir: Option<String>,
    active_skills: Option<Vec<String>>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mut params = serde_json::json!({ "task": task, "mode": mode, "autonomous": autonomous });
    if let Some(tid)    = thread_id    { params["thread_id"]    = serde_json::json!(tid); }
    if let Some(wd)     = work_dir     { params["work_dir"]     = serde_json::json!(wd); }
    if let Some(skills) = active_skills { params["active_skills"] = serde_json::json!(skills); }
    rpc(&state, "run_task", params)
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
// Map an MCP server id to the CANONICAL env var its subprocess reads for auth.
// None for no-auth servers. The npm github/gitlab servers read the
// *_PERSONAL_ACCESS_TOKEN names (NOT *_TOKEN); the daemon aliases the value to the
// legacy names too, so injecting the canonical one is sufficient.
fn mcp_token_env_var(server: &str) -> Option<&'static str> {
    match server {
        "github" => Some("GITHUB_PERSONAL_ACCESS_TOKEN"),
        // gitlab keeps GITLAB_TOKEN (the vendored env_required name); the daemon
        // aliases it to GITLAB_PERSONAL_ACCESS_TOKEN in the subprocess env too.
        "gitlab" => Some("GITLAB_TOKEN"),
        _ => None,
    }
}

// Read a saved MCP auth token from the OS keychain (account `mcp:<server>`, the
// same store the UI's secure field writes via save_api_key). Returns the env var
// name + token, or None for a no-auth server or when no token has been saved.
// SECURITY: the token is read here in Rust and handed straight to the daemon —
// it never enters the JS/React layer and is never logged.
fn read_mcp_secret(server: &str) -> Option<(&'static str, String)> {
    let var = mcp_token_env_var(server)?;
    let account = format!("mcp:{}", server);
    match keyring::Entry::new("hcode-v2-desktop", &account).ok()?.get_password() {
        Ok(token) if !token.is_empty() => Some((var, token)),
        _ => None,
    }
}

#[tauri::command]
async fn connect_mcp_server(server: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut params = serde_json::json!({ "server": server });
    // SECURITY (Phase 2): inject the keychain token as `secrets` over the daemon's
    // stdin (which is never echoed to stdout/events). The daemon registers it for
    // redaction and injects it into the spawned server's env only. No-auth servers
    // get no `secrets` key, so Phase 1 connects are unchanged.
    if let Some((var, token)) = read_mcp_secret(&server) {
        let mut secrets = serde_json::Map::new();
        secrets.insert(var.to_string(), serde_json::Value::String(token));
        params["secrets"] = serde_json::Value::Object(secrets);
    }
    rpc(&state, "connect_mcp_server", params)
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
// Presence-only check: does the keychain hold a value for this provider? Returns
// a bool and NEVER the secret itself, so the UI can show "auth ready" for a
// previously-saved token (incl. across sessions) without pulling it into JS.
#[tauri::command]
async fn has_api_key(provider: String) -> Result<bool, String> {
    match keyring::Entry::new("hcode-v2-desktop", &provider).map_err(|e| e.to_string())?.get_password() {
        Ok(k)                        => Ok(!k.is_empty()),
        Err(keyring::Error::NoEntry) => Ok(false),
        Err(e)                       => Err(e.to_string()),
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
        .manage(AppState { daemon: Mutex::new(daemon::DaemonSupervisor::new()) })
        .invoke_handler(tauri::generate_handler![
            start_daemon, stop_daemon, daemon_health,
            run_task, abort_task, approve_plan, reject_plan,
            accept_patch, reject_patch, rollback_all,
            list_skills, list_workflows, run_workflow,
            list_mcp_servers, connect_mcp_server, disconnect_mcp_server,
            open_folder_dialog, list_directory, read_file, write_file,
            save_api_key, get_api_key, has_api_key,
        ])
        .run(tauri::generate_context!())
        .expect("error running HCode v2 Desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    // Proves the FIX: with a native keyring backend enabled, a value SET under an
    // account is READABLE back from a fresh Entry. Without a platform-backend
    // feature, keyring v3 uses a non-persistent mock store and this returns
    // NoEntry -- which is why read_mcp_secret always returned None and connect
    // reported needs-auth. Uses a UNIQUE throwaway account (never "mcp:github")
    // so a user's real saved token is untouched, and cleans up after itself.
    #[test]
    fn keyring_backend_persists_across_entries() {
        let acct = "hcode-keyring-selftest:probe-9f3a";
        let secret = "probe-value-do-not-use";
        keyring::Entry::new("hcode-v2-desktop", acct).expect("entry")
            .set_password(secret).expect("set_password");

        let read = keyring::Entry::new("hcode-v2-desktop", acct).expect("entry").get_password();
        let _ = keyring::Entry::new("hcode-v2-desktop", acct).expect("entry").delete_credential();

        match read {
            Ok(v)  => assert_eq!(v, secret, "round-tripped value differs"),
            Err(e) => panic!("keyring round-trip failed ({e}) -- is a platform backend feature enabled?"),
        }
    }

    #[test]
    fn token_env_var_names_are_canonical() {
        assert_eq!(mcp_token_env_var("github"), Some("GITHUB_PERSONAL_ACCESS_TOKEN"));
        assert_eq!(mcp_token_env_var("gitlab"), Some("GITLAB_TOKEN"));
        assert_eq!(mcp_token_env_var("filesystem"), None);
    }

}
