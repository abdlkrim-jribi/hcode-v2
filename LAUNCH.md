# HCode v2 — Launch Guide

Two ways to run HCode v2 as a desktop app:

---

## A. Dev Launcher (recommended for demos and development)

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

## B. Full Tauri Desktop Build (production — C4)

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
