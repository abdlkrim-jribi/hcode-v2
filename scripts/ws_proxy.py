"""WebSocket ↔ stdio proxy for the HCode v2 daemon (dev-launcher mode).

Spawns `python -m hcode_v2.daemon` as a child process and bridges its
stdin/stdout to a WebSocket server.  This replicates in Python exactly
what Tauri's daemon.rs does in Rust so the React UI can talk to the
daemon in a plain browser without a Tauri shell.

Protocol (same as the Tauri path):
  Browser → WS → proxy stdin → daemon
  Daemon stdout → proxy → WS → browser

All sends to a WebSocket go through a per-client asyncio.Queue so
concurrent sends from daemon_reader and handle_client never race.

Usage:
    python scripts/ws_proxy.py [--port 1421] [--work-dir PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("ws_proxy")


async def run_proxy(work_dir: str, port: int) -> None:
    try:
        import websockets
        import websockets.server
    except ImportError:
        print(
            "[ws_proxy] ERROR: 'websockets' package not found.\n"
            "           Install it with: uv sync --group dev",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Spawn the daemon subprocess ───────────────────────────────────────────
    daemon_cmd = [sys.executable, "-m", "hcode_v2.daemon", "--work-dir", work_dir]
    logger.info("Starting daemon: %s", " ".join(daemon_cmd))

    proc = await asyncio.create_subprocess_exec(
        *daemon_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
        cwd=str(Path(__file__).parent.parent),  # hcode-v2 root
    )
    logger.info("Daemon PID: %d", proc.pid)

    # Per-client send queues: all writes to a ws go through its queue
    # so daemon_reader and handle_client never send concurrently.
    client_queues: dict[int, asyncio.Queue] = {}

    async def _sender(ws_id: int, ws) -> None:
        """Drain the send queue for one client."""
        q = client_queues[ws_id]
        try:
            while True:
                item = await q.get()
                if item is None:   # sentinel — stop the sender
                    break
                try:
                    await ws.send(item)
                except Exception as exc:
                    logger.debug("send error to client %d: %s", ws_id, exc)
        except Exception:
            pass

    def enqueue(ws_id: int, text: str) -> None:
        """Non-blocking push to a client's send queue."""
        q = client_queues.get(ws_id)
        if q is not None:
            q.put_nowait(text)

    def broadcast(text: str) -> None:
        for ws_id in list(client_queues):
            enqueue(ws_id, text)

    async def daemon_reader() -> None:
        """Read daemon stdout and broadcast each JSON line to all WS clients."""
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                logger.warning("Daemon stdout closed.")
                break
            text = line.decode().strip()
            if text:
                logger.debug("daemon → clients: %s", text)
                broadcast(text)

    _next_id = 0

    async def handle_client(websocket) -> None:
        nonlocal _next_id
        _next_id += 1
        ws_id = _next_id
        q: asyncio.Queue = asyncio.Queue()
        client_queues[ws_id] = q

        # Start the sender coroutine for this client
        sender_task = asyncio.create_task(_sender(ws_id, websocket))
        logger.info("Browser connected id=%d (total: %d)", ws_id, len(client_queues))

        # Synthetic ready — ensures App.tsx initialises even if the real
        # "ready" was emitted before this client connected.
        enqueue(ws_id, json.dumps({"type": "ready"}))

        try:
            async for message in websocket:
                logger.debug("client %d → daemon: %s", ws_id, message)
                # Intercept Tauri-level lifecycle commands that the daemon
                # does not expose as JSON-RPC methods.
                intercepted = False
                try:
                    req = json.loads(message)
                    method = req.get("method", "")
                    req_id = req.get("id")
                    if method in ("start_daemon", "daemon_health"):
                        resp = json.dumps({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "status": "running",
                                "pid": proc.pid,
                                "uptime": 0,
                                "version": "2.0.0",
                            },
                        })
                        enqueue(ws_id, resp)
                        intercepted = True
                    elif method == "stop_daemon":
                        proc.terminate()
                        enqueue(ws_id, json.dumps({"jsonrpc": "2.0", "id": req_id, "result": None}))
                        intercepted = True
                except Exception:
                    pass   # malformed JSON — forward as-is

                if not intercepted:
                    assert proc.stdin is not None
                    proc.stdin.write((message + "\n").encode())
                    await proc.stdin.drain()

        except Exception as exc:
            logger.debug("Client %d disconnected: %s", ws_id, exc)
        finally:
            q.put_nowait(None)   # stop the sender
            await sender_task
            client_queues.pop(ws_id, None)
            logger.info("Browser disconnected id=%d (total: %d)", ws_id, len(client_queues))

    # ── Start WebSocket server ────────────────────────────────────────────────
    reader_task = asyncio.create_task(daemon_reader())
    print(f"[ws_proxy] Daemon PID {proc.pid} — ws://localhost:{port}", flush=True)

    try:
        async with websockets.serve(handle_client, "localhost", port):
            await asyncio.Future()  # run until cancelled
    finally:
        reader_task.cancel()
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            proc.kill()
        logger.info("Proxy stopped.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="HCode v2 WS ↔ stdio proxy")
    parser.add_argument("--port", type=int, default=1421, metavar="PORT")
    parser.add_argument("--work-dir", default=".", metavar="DIR")
    args = parser.parse_args()
    work_dir = str(Path(args.work_dir).resolve())
    try:
        asyncio.run(run_proxy(work_dir, args.port))
    except KeyboardInterrupt:
        print("\n[ws_proxy] Stopped.", flush=True)


if __name__ == "__main__":
    main()
