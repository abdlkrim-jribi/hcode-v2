"""Container health check for the HCode v2 backend.

Opens a WebSocket to the running proxy and round-trips a JSON-RPC ``health``
request to the daemon. Exits 0 if the daemon reports ``status: running``,
non-zero otherwise. Used by the Dockerfile HEALTHCHECK and by docker-compose
to health-gate the frontend.

Connection target resolves from the same env the proxy binds to:
    host: 127.0.0.1 (always loopback inside the container)
    port: $HCODE_WS_PORT (default 8765)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

PORT = int(os.getenv("HCODE_WS_PORT", "8765"))
URL = f"ws://127.0.0.1:{PORT}"
DEADLINE_S = 4.0


async def _check() -> bool:
    try:
        import websockets
    except ImportError:
        print("[healthcheck] websockets not installed", file=sys.stderr)
        return False

    try:
        async with websockets.connect(URL, open_timeout=DEADLINE_S) as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health"}))
            # The proxy emits a synthetic {"type":"ready"} on connect; read a few
            # messages until we see the daemon's health result or time out.
            loop = asyncio.get_event_loop()
            end = loop.time() + DEADLINE_S
            while loop.time() < end:
                remaining = end - loop.time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                result = msg.get("result")
                if isinstance(result, dict) and result.get("status") == "running":
                    return True
    except Exception as exc:  # noqa: BLE001 — any failure means unhealthy
        print(f"[healthcheck] {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    return False


def main() -> None:
    ok = asyncio.run(_check())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
