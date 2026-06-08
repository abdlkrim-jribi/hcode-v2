# HCode v2 — Docker Delivery Guide

Run the full HCode v2 agent — backend, agent engine, and web UI — with **nothing installed but Docker**. No Python, no Node.js, no knowledge of the codebase required.

One command brings up two containers wired together; you open a browser and use the app.

---

## 1. Prerequisites

The **only** things you need on the host machine:

| Requirement | Check it's installed |
|---|---|
| Docker Engine 24+ | `docker --version` |
| Docker Compose v2 | `docker compose version` |

That's it. The Python backend and the Node-built frontend are baked into the images — you never install either on the host.

---

## 2. Quick start (live, with your model)

From the repository root:

```bash
# 1. Create your config from the template (the real .env is gitignored)
cp .env.docker.example .env

# 2. Edit .env — point it at your model endpoint:
#      HCODE_MOCK=0
#      HCODE_MODEL_NAME=<your model name>
#      HCODE_MODEL_API_KEY=<your gpt-oss / OpenAI-compatible key>
#      HCODE_MODEL_BASE_URL=<your endpoint base URL>

# 3. Put the code you want the agent to work on into ./workspace/
cp -r /path/to/your/project/* ./workspace/

# 4. Bring up the stack
docker compose up --build

# 5. Open the UI
#      http://localhost:8080
```

Type a task in the UI; the agent plans, edits files in `./workspace/`, and verifies — streaming each phase back to the browser.

To stop: `Ctrl+C`, then `docker compose down`.

---

## 3. Mock mode — try it with no API key

You can run the entire stack **without any model credentials**. Useful for a first look, demos, or CI.

In `.env`:

```ini
HCODE_MOCK=1
```

The backend daemon then emits **deterministic, scripted** plan/execute/verify events instead of calling a model. The browser still drives the real two-container WebSocket path — you see the full UI behaviour (streaming tokens, phase timeline, diff card) with zero cost and zero key.

```bash
docker compose up --build      # with HCODE_MOCK=1 in .env
# → http://localhost:8080 → type any task → watch the mock stream
```

**Even-more-offline option:** set `VITE_MOCK=true` in `.env` to make the *browser* use built-in fixtures and skip the backend entirely. (`HCODE_MOCK=1` is the better demo — it exercises the real container-to-container path.)

Switching mock ↔ live is **one line** in `.env` (`HCODE_MOCK=1` vs `HCODE_MOCK=0` + the `HCODE_MODEL_*` values). No rebuild of the images is needed — config is read at container start.

---

## 4. The workspace volume — where your code lives

**`./workspace/` on the host is the single folder the agent reads and writes.**

```
  ./workspace   (host)   ⟷   /workspace   (inside the backend container)
```

- Put the project you want the agent to work on **inside `./workspace/`** before (or while) the stack is running.
- Everything the agent creates, edits, or deletes happens **here**, on your host disk — changes persist after the containers stop.
- It is a Docker **bind mount**: there is no copy step, no syncing. The container sees your host folder directly.
- It is the **only writable mount**. The rest of each image is read-only application code.

If `./workspace/` doesn't exist, Docker creates it on first `up`. A `.gitkeep` keeps the folder in the repo; its contents are gitignored so your code is never accidentally committed.

---

## 5. How the two containers talk (host-vs-container URL)

The frontend serves the UI; the **browser** then opens a WebSocket straight to the backend. Because that connection is made by the browser **on your host machine**, the URL must be the host-mapped port:

```
  HCODE_WS_URL = ws://localhost:8765      ✅  the browser can reach this
  HCODE_WS_URL = ws://backend:8765        ❌  only resolves inside Docker; the
                                              browser has no route to it
```

`ws://localhost:8765` is the default in `.env.docker.example` and you should not need to change it for a single-host deployment. (`backend` is a Docker-internal DNS name — using it makes the UI load but the WebSocket never connect. This is the single most common mistake.)

---

## 6. Semantic verification (LSP)

The backend image bundles a **language server** so the agent can verify its own code *semantically* — not just by reading tool output, but by type- and syntax-checking the files it edited. During the **Verify** phase, the agent runs the language server on the files it changed this task; if there are **errors**, they are fed back and the agent loops to fix them (self-correction).

- **Supported today: Python**, via [pyright](https://github.com/microsoft/pyright) — pinned to **`1.1.410`** to match what was tested in local dev, so container diagnostics are identical. The client is language-agnostic; other servers (TypeScript, Rust, Go) are added as configuration, not new code.
- **Errors only.** Only true type/syntax **errors** trigger a re-execution. Warnings and style hints are reported but never cause a loop, so the agent never churns on benign findings.
- **No key, no network.** The language server runs entirely **locally inside the container** — no API key, no internet. It works in mock mode and in air-gapped deployments.
- **Optional by design — graceful degradation.** LSP is an *additional* signal, never a precondition. For a file type with no configured server, Verify behaves exactly as it does without it. The image ships pyright on `PATH`, so it is **on** by default for Python; to build a leaner image without it, drop the `lsp` stage and its two `COPY` lines from `docker/Dockerfile.backend` — the agent detects the missing server and Verify falls back to its text-only behaviour with **zero regression**.

This costs **~120 MB** in the backend image (the Node runtime + pyright): roughly **311 MB → 429 MB**.

---

## 7. Operating the stack

| Action | Command |
|---|---|
| Start (build if needed) | `docker compose up --build` |
| Start in background | `docker compose up -d` |
| Stop and remove containers | `docker compose down` |
| Follow logs (both services) | `docker compose logs -f` |
| Logs for one service | `docker compose logs -f backend` |
| Service status + health | `docker compose ps` |
| Rebuild after a code change | `docker compose build` |

The frontend is **gated** on the backend: Compose starts `frontend` only after `backend` reports **healthy** (`docker compose ps` shows `Up (healthy)`). The health check round-trips a request over the backend's WebSocket every 15s.

---

## 8. Troubleshooting

**Port already in use (`8080` or `8765`)**
Another process holds the port. Either stop it, or remap in `docker-compose.yml`:
```yaml
  frontend:
    ports: ["9090:80"]      # use http://localhost:9090
  backend:
    ports: ["9765:8765"]    # then set HCODE_WS_URL=ws://localhost:9765 in .env
```
Remember: if you change the backend host port, update `HCODE_WS_URL` to match.

**UI loads but never connects / no streaming**
The browser can't reach the backend WebSocket. Check that `HCODE_WS_URL` is `ws://localhost:8765` (the host port) and **not** `ws://backend:8765`. Confirm the backend is healthy: `docker compose ps`.

**Backend stuck "unhealthy" / frontend never starts**
The health check can't reach the daemon. Inspect: `docker compose logs backend`. In live mode a bad `HCODE_MODEL_*` value can stall startup — try `HCODE_MOCK=1` first to confirm the plumbing, then add credentials.

**"daemon not running" in the UI**
The backend container isn't up or isn't healthy yet. `docker compose ps`; wait for `Up (healthy)`; check `docker compose logs -f backend`.

**Changes to `.env` not taking effect**
`.env` is read at container start. Re-create the containers: `docker compose down && docker compose up`.

---

## 9. Security notes

- **The API key is runtime-only.** It is supplied via `.env` / environment at container start and is **never baked into any image**. You can publish or share the images without leaking credentials.
- **`.env` is gitignored** and excluded from the Docker build context (`.dockerignore`) — it cannot end up in version control or inside an image.
- **The frontend container never receives the key.** Only `backend` reads `.env`; the frontend gets just the WebSocket URL and the mock toggle.
- **`./workspace/` is the only writable mount.** The rest of each image is read-only application code. The backend runs as a **non-root** user.
- **No image contains a secret.** Verified: no API key, token, or password literal appears in any Dockerfile or in `docker-compose.yml`.

---

## 10. Verification checklist (zero → working, clean machine)

Run these in order on a machine that has **only Docker** installed. Each step has a concrete pass condition.

**Mock mode (no key):**

1. `docker --version && docker compose version` → both print versions. *(prereqs met)*
2. `cp .env.docker.example .env` and confirm `HCODE_MOCK=1` is set. *(mock config in place)*
3. `docker compose up --build` → images build; logs show `backend ... Healthy` **then** `frontend ... Started`. *(health gating works)*
4. `docker compose ps` → `backend` is `Up (healthy)`, `frontend` is `Up`. *(both running)*
5. Open `http://localhost:8080` → the HCode UI loads (Explorer, Agent panel, task box). *(frontend served)*
6. Type a task (e.g. "create a hello.py file") and submit → the Agent panel streams **Plan → Execute → Verify** with a plan card and a diff. *(full path: browser → frontend → ws://localhost:8765 → backend → mock daemon)*
7. *(Optional, LSP)* `docker compose exec backend pyright --version` → `pyright 1.1.410`. *(the language server is bundled and on `PATH`, so Verify can semantically check Python — see §6)*

**Switch to live (with a key):**

8. `docker compose down`; in `.env` set `HCODE_MOCK=0` and fill `HCODE_MODEL_NAME` / `HCODE_MODEL_API_KEY` / `HCODE_MODEL_BASE_URL`; put real code in `./workspace/`.
9. `docker compose up` → repeat steps 4–6; the agent now performs the task for real and the edits appear under `./workspace/` on the host. *(live model + persistent edits)*

---

## 11. Offline / air-gapped delivery

For a security-conscious or disconnected environment, build the images once on a connected machine, then ship them as files — no registry, no internet on the target host. See **`scripts/build-images.sh`** and the `docker save` / `docker load` instructions in that script's header.
