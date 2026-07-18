> **Cross-reference:** this is the *original* discovery writeup from the first native
> rebuild + smoke pass (2026-07-09) — it documents how the plan-review and no-daemon
> bugs were found, both since fixed (PR #117, PR #120). It was briefly stranded on an
> unpushed branch (`fix/native-desktop-delivery`) and rescued here under a distinct
> filename after a later doc of the same name (`docs/native-smoke-2026-07.md`,
> from `fix/daemon-failure-visibility`) silently replaced it on `dev`. See that file
> for the C2 (daemon-failure-visibility) verification pass.

# Native desktop smoke test — 2026-07-09

Branch: `fix/native-desktop-delivery` off `dev @ 7d665d4` (dev synced from `origin/dev`,
which had already absorbed `feat/project-map-context` via PR #116 — one commit ahead of
what CLAUDE.md's last refresh recorded).

Context: `target/release/hcode-v2-desktop.exe` was compiled 2026-06-17, `target/debug`
2026-06-28 — both predate abort, model selection, 429 fallback, plan-review, the harness
fixes, and project-map-context. Source at HEAD is current; the goal of this pass was to
rebuild from source, smoke-test the result, and ship a fresh installer.

## 1. Hygiene commit

`desktop-app/src-tauri/Cargo.lock` is now tracked (it was never committed — non-reproducible
builds). `.gitignore` extended for `src-tauri/gen/`, `.hcode/`, `mcp_test/`, `*.db`.
Deleted `task_manager.py` / `test_task_manager.py` from `src-tauri` — verified by reading
both first: a generic, unrelated "task manager" demo app with no connection to this
project, almost certainly agent-generated scratch litter. Commit `1014de8`.

## 2. `cargo test` (src-tauri)

```
running 2 tests
test tests::keyring_backend_persists_across_entries ... ok
test tests::token_env_var_names_are_canonical ... ok
test result: ok. 2 passed; 0 failed; 0 ignored
```

Both pass, including the keyring round-trip test that guards the MCP secure-token feature.

## 3. `cargo tauri dev` smoke list (against the real daemon, live model)

`.env` had `HCODE_MOCK=0` and a real OpenRouter key configured (`z-ai/glm-5.2`). Note:
**`HCODE_MOCK` has zero effect on the native app** — `DaemonSupervisor::find_daemon_command`
(`desktop-app/src-tauri/src/daemon.rs`) never passes a `--mock` flag; the native path is
*always* live. `HCODE_MOCK` only affects `scripts/dev.py` / the Docker stack. This is worth
documenting somewhere user-facing — today it's a silent gap between what `.env`'s own
comments promise ("Docker stack configuration... Two modes...") and what the native app
actually honors.

| # | Item | Result |
|---|---|---|
| 1 | Open folder → file tree | ✅ PASS |
| 2 | Run task, `work_dir` forwarded | ✅ PASS — tool-call paths in the transcript, and the actual file edit on disk, landed in the opened folder, not the app's cwd |
| 3 | Stop mid-run | ✅ PASS — aborted cleanly mid-`bash`-tool-call, transcript shows `_(aborted)_`, two pending tool-call rows stayed unresolved |
| 4 | Pick a model from the dropdown | ⚠️ INCONCLUSIVE — the model/session `<select>` popups render via a separate `msedgewebview2.exe` process; access to that process was requested and denied. Verified by source review instead: `list_models` is a plain metadata GET (no completion cost), `fallback_models()` is documented never-empty, and `main.rs`/`bridge.ts` forward the Tauri command → RPC correctly. Not independently visually confirmed. |
| 5 | Toggle Plan Review → accept → run again → reject | ❌ **FAIL — confirmed regression, out of scope to fix here** (see below) |
| 6 | MCP: connect filesystem | ✅ PASS — "Connected · 14 tools" |
| 7 | MCP: github needs-auth / gitlab needs-auth → save token → connect | ✅ PASS — token save round-trips through the OS keychain (survived an app restart mid-session); connecting with a deliberately invalid token is rejected gracefully ("Saved token was not accepted. Re-enter it below.") with no contradictory auth state |

### Plan Review regression (confirmed, NOT fixed — out of scope)

Reproduced independently of the UI: a raw JSON-RPC probe directly against
`python -m hcode_v2.daemon` with `plan_review: true` never emits a `plan_review` event —
the run goes `planning_started → tool calls → done` in one shot, and a follow-up
`resume_plan` RPC returns `{"status": "no_pending_plan"}`. The native UI shows the same
behavior (checkbox toggles fine; the run never pauses for accept/reject). Cache-eviction
logic in `server.py` (rebuild agent when `plan_review` changes) is confirmed correct — the
bug is inside `PlanReviewMiddleware`/the PEV phase-interrupt contract, i.e.
`src/hcode_v2/agent/plan_review.py` and friends — explicitly out of scope for this task
(coordination zone, Python agent-core, off-limits per instructions). Flagged as a separate
background task for follow-up with Abdelkrim.

## 4. Fixes applied

None required within scope (`desktop-app/`). The one confirmed break (Plan Review) lives
entirely in `src/hcode_v2`, which this task was scoped to leave untouched.

## 5. `cargo tauri build` + installer verification

Build succeeded cleanly:

```
Finished `release` profile [optimized] target(s) in 2m 06s
Built application at: target/release/hcode-v2-desktop.exe
Finished 2 bundles at:
    target/release/bundle/msi/HCode v2_2.0.0_x64_en-US.msi
    target/release/bundle/nsis/HCode v2_2.0.0_x64-setup.exe
```

The NSIS installer (`HCode v2_2.0.0_x64-setup.exe`) ran to completion unattended (one-click
mode — no wizard prompts observed) and installed to
`C:\Users\<user>\AppData\Local\HCode v2\` (`hcode-v2-desktop.exe` + `uninstall.exe`,
timestamped today — confirmed fresh, not the stale June binary).

**The installed app launches** (process starts, stays alive, window renders — confirmed via
a direct screen capture since the window's WebView2 popups couldn't be granted through the
normal path either) **but the daemon never starts**, and so no story — mock or live — can
play at all from the installed location:

- Process inspection shows `hcode-v2-desktop.exe` running with **no** `uv.exe` or
  `python.exe` child process, ever.
- The app's own status bar, which shows a `● Daemon` pill whenever the daemon is reachable
  (seen consistently throughout the `cargo tauri dev` testing above), shows **no daemon
  indicator at all** when launched from the installed location — just `○ Idle`.
- Root cause: `DaemonSupervisor::find_daemon_command` (`daemon.rs`) checks, in order: (1) a
  bundled `hcode-v2-daemon.exe` next to the executable — **not present**, this build never
  produces one; (2) `uv run python -m hcode_v2.daemon` — `uv` resolves a project by walking
  up from the current directory for a `pyproject.toml`; `C:\Users\<user>\AppData\Local\HCode
  v2\` has no such ancestor, so this fails immediately; (3) plain `python -m hcode_v2.daemon`
  — same problem, `hcode_v2` isn't installed globally. The subprocess spawn either errors
  immediately or the module import fails, and daemon.rs surfaces **no error to the user** —
  the UI just silently never shows a daemon connection.

This is **only true for the installed/standalone build launched outside the source tree** —
everything upstream in this report (`cargo tauri dev`, all abort/MCP/task-execution testing)
ran from inside the project checkout, where `uv run` resolves `hcode-v2/pyproject.toml` via
its cwd fine. `cargo tauri dev`'s `BeforeDevCommand`/`DevCommand` always run with cwd
`desktop-app/src-tauri`, itself nested under the project — so this gap was invisible during
dev-mode testing and only surfaces once the exe is copied/installed elsewhere.

**This means today's `cargo tauri build` output is not yet a complete standalone
deliverable** — it's a real functional gap, not a smoke-test nit, and definitely bigger than
"fix what's broken" scope for this pass. Flagged as a separate follow-up task (see below)
rather than attempted here.

## 6. Follow-ups filed (not fixed in this branch)

1. **Plan Review interrupt never fires** — `src/hcode_v2/agent/plan_review.py` /
   `factory.py` / PEV phase contract. Coordination zone; flagged for Abdelkrim.
2. **Installed app has no working daemon** — needs either a bundled `hcode-v2-daemon.exe`
   (PyInstaller or similar, checked first by `daemon.rs`'s own discovery order — the hook
   already exists, nothing is wired to produce the binary) or `daemon.rs` needs to resolve
   the project path some other way (e.g. an embedded resource pointing at a known install
   layout) when running standalone. This blocks any real demo of the installed
   `.exe`/`.msi` outside a dev checkout. Given ownership (GUI/daemon/Docker/Rust is the
   user's own track), this is theirs to prioritize, not Abdelkrim's — flagged here rather
   than as a spawned task.
3. Minor, not fixed (not in the explicit smoke checklist): clicking a folder under
   **Recent** in the START screen doesn't reopen it — only the **Open Folder** button /
   native dialog path works. Low severity, easy to reproduce, left for a future pass.

## 7. Commits on this branch

- `1014de8` — `chore(desktop): track Cargo.lock, ignore build/demo scratch, drop unrelated litter`
- `6f91707` — `docs(desktop): record native rebuild smoke-test results`
