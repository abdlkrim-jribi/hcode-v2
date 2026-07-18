# Native smoke — desktop app

Manual native verification of the Tauri desktop app (`cargo tauri dev`), recorded
per feature. Each entry is a real run against the real daemon, not the mock.

> **Cross-reference:** for the *original* discovery writeup of the plan-review and
> no-daemon bugs this fix addresses (from the first native rebuild pass, 2026-07-09),
> see `docs/native-smoke-discovery-2026-07.md`.

---

## fix/daemon-failure-visibility (2026-07)

Goal: a daemon that fails to start must be **loud** — no more green/neutral pill
over a dead brain. Verified natively via `cargo tauri dev` on branch
`fix/daemon-failure-visibility`. `cargo test` (src-tauri): 4 passed, incl. the two
new daemon tests (`stderr_tail_caps_and_keeps_latest`,
`dead_daemon_before_ready_errors_and_captures_stderr`).

### a. Healthy path — ready-gated `starting → Connected`  ✅ PASS

`cargo tauri dev` with the daemon module intact.

- Daemon pill settled on green **`● Connected`** — reached only via the daemon's
  `{"type":"ready"}` line flipping the shared status to `running` (not a
  hardcoded "running" on spawn as before).
- Dev console: no `[Daemon] reader exited`, single daemon process (the
  `start()` guard now turns away the duplicate `start_daemon` React StrictMode
  fires in dev, so no second daemon is spawned and orphaned).
- No error banner.

### b. Broken daemon — `error` pill + actionable banner  ✅ PASS

Broke the spawn on purpose by temporarily renaming
`src/hcode_v2/daemon/__main__.py` (restored immediately after), so
`uv run python -m hcode_v2.daemon` starts Python, fails to import, and exits
before ever emitting `ready`.

- Within the run, the daemon pill flipped to red **`○ Daemon`** (Error).
- A dismissible banner appeared at the top of the window:

  > ⚠ Daemon failed to start. The daemon process exited before it became ready.
  > ▸ Show details

- Expanding **Show details** rendered the discovery tried-list and the captured
  process stderr — the exact reason, verbatim:

  ```
  Tried (in order):
    - bundled binary: \\?\C:\dev\PFE\hcode-v2\desktop-app\src-tauri\target\debug\hcode-v2-daemon.exe
    - bundled binary: \\?\C:\dev\PFE\hcode-v2\desktop-app\src-tauri\target\debug\hcode-v2-daemon
    - uv run python -m hcode_v2.daemon

  Daemon stderr (last 1 line):
  C:\dev\PFE\hcode-v2\.venv\Scripts\python.exe: No module named
  hcode_v2.daemon.__main__; 'hcode_v2.daemon' is a package and cannot be
  directly executed
  ```

  This matched the dev console line exactly:
  `[Daemon stderr] ...: No module named hcode_v2.daemon.__main__ ...`
  proving the stderr reader (previously never drained) captured the tail and
  the `daemon-error` event carried it to the UI. `__main__.py` was renamed back
  and `git status` confirmed no residual change to the daemon package.

Before this fix, both scenarios rendered the same silent, neutral/green pill with
no message. Now the failure is impossible to miss and self-explaining.
