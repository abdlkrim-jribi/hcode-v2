# HCode v2 — Launch Guide

Three ways to run HCode v2 as a desktop app:

---

## A. One-click Desktop Launch (Windows) — recommended for demos

No terminal needed after first-time setup. Double-click to start the full stack.

### First-time setup (run once)

```powershell
# 1. Install prerequisites (Python + uv + Node) — see section B below if needed.

# 2. Create the Desktop shortcut (run from the hcode-v2 root):
powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
```

This places an **HCode v2** icon on your Desktop.

### Icon files

Drop the following into `desktop-app/assets/` before running `create-shortcut.ps1`
to get the branded icon on the Desktop shortcut:

```
desktop-app/assets/hcode-icon.ico   ← required for the Windows shortcut icon
desktop-app/assets/hcode-icon.svg   ← optional (used by the React UI)
desktop-app/assets/hcode-icon-*.png ← optional (used by the React UI)
```

If the `.ico` is absent, the shortcut is still created with the default Windows icon.
You can drop the file in later and re-run `create-shortcut.ps1` to update it.

### Launching

Double-click **HCode v2** on your Desktop.

The launcher (`scripts/launch.ps1`) auto-detects which mode to use:

| `.env` state | Mode | What starts |
|---|---|---|
| No `.env` file | **MOCK** | `python scripts/dev.py --mock` |
| `.env` exists but `HCODE_MODEL_API_KEY` is a placeholder | **MOCK** | `python scripts/dev.py --mock` |
| `.env` exists with a real API key | **LIVE** | `python scripts/dev.py` |

In **MOCK mode** a yellow banner is printed:

```
┌─────────────────────────────────────────────────────────────┐
│  MOCK MODE — offline demo, no API key needed                │
│                                                             │
│  Running in MOCK mode.                                      │
│  Add your API key to .env and relaunch to go live.          │
└─────────────────────────────────────────────────────────────┘
```

To go **live**, edit `.env` and set:

```env
HCODE_MODEL_API_KEY=<your-real-key>
HCODE_MODEL_NAME=gpt-4o-mini
HCODE_MODEL_BASE_URL=https://your-endpoint.example.com/v1   # if self-hosted
```

Then double-click the Desktop icon again — it auto-switches to LIVE mode.

### Running without the Desktop shortcut

If you prefer a terminal:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\launch.ps1
```

Or just double-click `scripts\launch.bat` in Explorer.

---

## B. Dev Launcher (command-line, cross-platform)

No Rust/Tauri build needed.  One command starts everything.

### Prerequisites

```bash
# Python side
pip install uv
cd hcode-v2 && uv sync --group dev     # installs websockets + all deps

# Node side
cd desktop-app/src-ui && npm install   # first time only
```

### Configure the model

```bash
cp .env.example .env
# Edit .env and set:
HCODE_MODEL_NAME=gpt-4o-mini           # or your model
HCODE_MODEL_API_KEY=sk-...             # your API key
HCODE_MODEL_BASE_URL=https://...       # for self-hosted (optional)
```

### Launch

```bash
# Real daemon + real model (streaming, diff cards, phase timeline):
python scripts/dev.py --work-dir /path/to/project

# Offline demo (VITE_MOCK=true, no model needed):
python scripts/dev.py --mock
```

This opens `http://localhost:1420` in your browser. The app is fully functional:
- Type a task → streaming tokens appear in the Agent panel
- Phase timeline advances: Plan → Execute → Verify
- File writes show a diff card in the editor

### What runs:

```
scripts/ws_proxy.py          ← spawns python -m hcode_v2.daemon
                               bridges its stdin/stdout to ws://localhost:1421
desktop-app/src-ui (vite)    ← React app at http://localhost:1420
                               VITE_WS_URL=ws://localhost:1421
```

The bridge.ts WS path is identical to what Tauri's `daemon.rs` does in Rust.

---

## C. Full Tauri Desktop Build (production — C4)

Produces a native `.exe` installer.  Requires Rust + WebView2.

### Prerequisites

```bash
# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup target add x86_64-pc-windows-msvc   # Windows

# Tauri CLI
cargo install tauri-cli

# Node (already done for Option A)
cd desktop-app/src-ui && npm install
```

### Dev mode (hot-reload)

```bash
cd desktop-app/src-tauri
cargo tauri dev
```

This runs the Vite dev server + the Tauri shell in one command.
The shell spawns `uv run python -m hcode_v2.daemon` automatically.

### Production build

```bash
cd desktop-app/src-tauri
cargo tauri build
# Output: target/release/bundle/
```

### Why this wasn't done during the sprint

`cargo build` takes 10-20 minutes on first run and pulls ~500 MB of Rust crates.
CLAUDE.md rule: never run Tauri/Rust compilation without explicit user confirmation.
The dev launcher covers 100% of the defense demo requirements without it.

---

## Smoke test (end-to-end with real model)

After `python scripts/dev.py --work-dir .`:

1. Browser opens at `http://localhost:1420`
2. Type task: `create a hello.py that prints Hello HCode`
3. Expected:
   - Phase timeline: ○ → 🔵 Planning → 🟡 Executing → 🟢 Verifying → ✅
   - Streaming tokens appear in the Agent panel
   - A diff card appears for `hello.py` (or `src/output.py`)
   - Status bar shows `verifying` then `done`
4. Find the created file in your work dir.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `websockets` not found | `uv run --group dev pip install websockets` |
| Vite port 1420 in use | `python scripts/dev.py --ui-port 1422` |
| WS port 1421 in use | `python scripts/dev.py --ws-port 1422` |
| `HCODE_MODEL_API_KEY` not set | Edit `.env` and set the key |
| Daemon exits immediately | Check stderr — likely a missing dep: `uv sync` |
