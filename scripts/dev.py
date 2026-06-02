"""HCode v2 Dev Launcher — one command to start the full stack.

Starts:
  1. ws_proxy.py  — spawns the Python daemon and bridges to a WebSocket port
  2. Vite dev server — serves the React UI at http://localhost:1420
  3. Opens the browser

Usage:
    # Full stack (real daemon, real model — set HCODE_MODEL + key in .env first):
    python scripts/dev.py

    # Mock mode (no model needed, offline demo):
    python scripts/dev.py --mock

    # Custom work dir (files the agent works on):
    python scripts/dev.py --work-dir /path/to/project

Environment (.env in hcode-v2/):
    HCODE_MODEL_NAME      model name, e.g. gpt-4o-mini
    HCODE_MODEL_API_KEY   OpenAI-compatible API key
    HCODE_MODEL_BASE_URL  base URL for self-hosted endpoint (optional)

Requirements:
    uv (Python side)   — pip install uv
    node / npm (UI)    — https://nodejs.org
    npm install        — run once in desktop-app/src-ui/
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT    = Path(__file__).parent.parent         # hcode-v2/
UI_DIR  = ROOT / "desktop-app" / "src-ui"
ENV_FILE = ROOT / ".env"

UI_PORT = 1420
WS_PORT = 1421

# On Windows, npm is npm.cmd — subprocess.Popen won't find bare "npm"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def check_requirements(mock: bool) -> bool:
    ok = True
    if not shutil.which("node"):
        print("ERROR: 'node' not found. Install Node.js from https://nodejs.org", file=sys.stderr)
        ok = False
    if not shutil.which(NPM):
        print("ERROR: 'npm' not found.", file=sys.stderr)
        ok = False
    if not (UI_DIR / "node_modules" / ".bin" / "vite").exists() and \
       not (UI_DIR / "node_modules" / ".bin" / "vite.cmd").exists():
        print(f"INFO: node_modules not installed. Running 'npm install' in {UI_DIR} …")
        subprocess.run([NPM, "install"], cwd=UI_DIR, check=True)
    if not mock and ENV_FILE.exists():
        env_text = ENV_FILE.read_text()
        if "HCODE_MODEL_API_KEY" not in env_text and "OPENAI_API_KEY" not in env_text:
            print(
                "WARNING: No API key found in .env.\n"
                "         Set HCODE_MODEL_API_KEY (or OPENAI_API_KEY) to use a real model.\n"
                "         Run with --mock to skip the daemon entirely.",
                file=sys.stderr,
            )
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HCode v2 Dev Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--work-dir", default=".",
        metavar="DIR",
        help="Working directory for the agent daemon (default: current dir)",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use VITE_MOCK=true — no daemon, no model, offline UI demo",
    )
    parser.add_argument(
        "--ws-port", type=int, default=WS_PORT,
        metavar="PORT",
        help=f"WebSocket proxy port (default: {WS_PORT})",
    )
    parser.add_argument(
        "--ui-port", type=int, default=UI_PORT,
        metavar="PORT",
        help=f"Vite dev server port (default: {UI_PORT})",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not open a browser window automatically",
    )
    args = parser.parse_args()

    work_dir = str(Path(args.work_dir).resolve())

    print()
    print("╔══════════════════════════════════════╗")
    print("║       HCode v2 Dev Launcher          ║")
    print("╚══════════════════════════════════════╝")
    print(f"  Mode     : {'MOCK (no model)' if args.mock else 'LIVE (real daemon)'}")
    print(f"  Work dir : {work_dir}")
    if not args.mock:
        print(f"  Daemon WS: ws://localhost:{args.ws_port}")
    print(f"  UI URL   : http://localhost:{args.ui_port}")
    print()

    if not check_requirements(args.mock):
        sys.exit(1)

    processes: list[subprocess.Popen] = []

    vite_env = {
        **os.environ,
        "VITE_MOCK": "true" if args.mock else "false",
        "VITE_WS_URL": f"ws://localhost:{args.ws_port}",
    }

    try:
        # ── Step 1: Start WS proxy (real mode only) ───────────────────────────
        if not args.mock:
            print("[1/2] Starting daemon WS proxy …")
            proxy = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "ws_proxy.py"),
                    "--port", str(args.ws_port),
                    "--work-dir", work_dir,
                ],
                cwd=str(ROOT),
                # inherit stderr so daemon logs appear in terminal
            )
            processes.append(proxy)
            time.sleep(1.5)   # give the daemon time to print "ready"
            if proxy.poll() is not None:
                print("ERROR: WS proxy exited immediately. Check the daemon logs above.", file=sys.stderr)
                sys.exit(1)
            print(f"       WS proxy running (PID {proxy.pid})")
        else:
            print("[1/2] Mock mode — daemon not started.")

        # ── Step 2: Start Vite dev server ─────────────────────────────────────
        print(f"[2/2] Starting Vite dev server (http://localhost:{args.ui_port}) …")
        vite_cmd = [NPM, "run", "dev", "--", "--port", str(args.ui_port)]
        vite = subprocess.Popen(vite_cmd, cwd=str(UI_DIR), env=vite_env)
        processes.append(vite)
        time.sleep(2.5)
        if vite.poll() is not None:
            print("ERROR: Vite exited immediately. Run 'npm install' in desktop-app/src-ui/", file=sys.stderr)
            sys.exit(1)
        print(f"       Vite running (PID {vite.pid})")

        # ── Open browser ──────────────────────────────────────────────────────
        url = f"http://localhost:{args.ui_port}"
        if not args.no_browser:
            print(f"\nOpening {url} …")
            webbrowser.open(url)

        print()
        print("Press Ctrl+C to stop all processes.")
        print()

        vite.wait()

    except KeyboardInterrupt:
        print("\n\nShutting down …")

    finally:
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=4)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("All processes stopped.")


if __name__ == "__main__":
    main()
