# Ground-truth correction — the shared working contract (2026-07-15)

This is a corrected extract of the local, workspace-root `CLAUDE.md` guidance file (not committed to
any repo — it lives one level above this checkout and is gitignored, so it's normally only visible in
Mohamed's own sessions). It's committed here **specifically so Abdelkrim can see it**, because several
of its previous claims were wrong — including one that directly concerns the vendored-freeze policy
(Rule 14 / §9 below) — and the correction shouldn't live somewhere only one of us can read.

Everything below was corrected against real `git log`/`gh pr list` output, not against the previous
version of the doc. Where the old doc's claim was false, that's stated explicitly rather than silently
fixed, so the correction itself is auditable.

**Refreshed 2026-07-15** against `dev @ 18260d9`. Previous version described a 2026-07-08 state and
predated PRs #117–#122.

---

## 1. Workspace Layout (Mohamed's local convention — for context, not enforced)

```
/c/dev/PFE/
  CLAUDE.md              ← the local file this doc corrects (auto-read on startup, not committed)
  context.md             ← full project history, PR registry — STALE (last refresh 2026-06-06)
  hcode-v2/              ← LINE OF RECORD — all new work lands here (this repo)
  hcode/                 ← v1, READ-ONLY reference, frozen on main. Never modify.
  outputs/               ← generated reports (audits, rapport-context docs) — not committed
```

**Enterprise constraint:** company model is self-hosted, OpenAI-compatible, `gpt-oss` family.
Must use LangChain + LangGraph. `hcode-v2` satisfies this; `hcode` (v1) does not.

**Free-tier reality (this matters for how dev/demo work happens):** the enterprise `gpt-oss` key is
never actually available to us — client testing happens on the client's own infrastructure, with
their key, not ours (see D1 below, still blocked). Day-to-day dev and demo work runs against **free
tiers of other providers**, and that has real, verified constraints:
- **Groq `gpt-oss-120b`** is the working dev/demo provider (same model family as the enterprise
  target, with functioning native tool-calling). Its free tier caps at **~8,000 tokens per
  request** (input + `max_tokens` counted together).
- HCode's un-slimmed prompt was measured at **~12,400 tokens per call** — every call 413'd. Fixed by
  prompt-slimming (`HCODE_SLIM_PROMPT`, PR #121, §9) — the prompt must stay under the free-tier
  ceiling for any free provider to be usable at all.
- Other free providers were bake-off tested: Gemini (`gemini-flash-latest`) fails at the tool-calling
  wire level (`thought_signature` requirement, OpenAI-compat shim doesn't send it); NVIDIA NIM's
  hosted `gpt-oss-120b` endpoint hangs indefinitely (confirmed dead at the transport level, not just
  slow); NVIDIA's `nemotron-super-49b` hallucinates task completion without calling any tools; Cerebras
  `gpt-oss-120b` works but is rate-limited (~5 RPM) and fails by **silent stall**, not a clean 429 —
  the reason cross-provider fallback (PR #122, §9) had to be timeout-triggered, not 429-only-triggered.

---

## 2. Current State

### hcode-v2 — mature, multi-surface, still under active co-development

| Branch | Commit | State |
|---|---|---|
| `dev` | `18260d9` | 300 commits since project start (2026-05-26). **579 passed, 1 skipped** on a clean checkout (see §8 for the one caveat re: a local-only test failure some checkouts show). |
| `main` | `260b118` | Stable pre-migration snapshot from 2026-05-30 — 14 commits behind `dev`. Promotion still gated on D1 (see §6) — unchanged since the last refresh. |
| `fix/rescue-cargo-lock-and-smoke-doc` | PR **#123**, **OPEN, not merged** | Rescues two things that were stranded on an unpushed branch: (1) `desktop-app/src-tauri/Cargo.lock` was never tracked on `dev` — non-reproducible builds — now regenerated fresh and committed; (2) the original 2026-07-09 smoke-test discovery writeup (how the plan-review and no-daemon bugs were first found) had been silently overwritten by a same-named doc from a later branch — recovered under a distinct filename. **Do not assume this is merged** — verify with `gh pr view 123` before relying on either fix being present. |
| `feat/tauri-build`, `feat/ui-redesign` | local-only, never pushed | Orphaned WIP found during a git reconciliation pass — 3 commits (icon generation, likely superseded by icons already on `dev`) and 1 commit (font packages, self-described as "parked, no component changes made") respectively. Neither is lost *finished* work; flagging so they don't get silently deleted or silently assumed-merged. |

**`dev` is co-developed:** GUI / daemon / Docker / Rust on one side, CLI / agent-core on the other.
Coordinate before merging anything that touches shared core (see Rule 12 — the coordination-zone file
list has grown since the last refresh).

**Feature arc landed since the previous refresh (2026-07-08 → 2026-07-15), in delivery order — every
one of these is a real merged PR, verified via `gh pr list`/`git log`, not a claim:**
```
#117 fix(agent): plan-review reachability — the HITL pause was SHIPPED BROKEN in #111 (see the
     corrected §7 row) and stayed broken through the previous refresh; this fix made it actually pause.
→ #118 feat(agent): honest Plan/Fast mode toggle — the composer buttons were DECORATIVE; the daemon
     silently ignored params["mode"]. Now wired to force the full plan→execute→verify arc.
→ #119 feat(agent): post-edit LSP gate — a phase-independent type-check safety net on every write/edit,
     not just inside PEV Verify.
→ #120 fix(desktop): daemon-failure visibility — a dead/never-started daemon now surfaces a loud error
     pill + banner with the stderr tail, instead of a silent green/neutral state.
→ #121 feat(agent): prompt slimming (HCODE_SLIM_PROMPT) — phase-scopes skills, drops dead vendored
     prompt sections and an unused tool, optionally caps completion tokens — the free-tier unlock.
→ #122 feat(provider): cross-provider fallback — a fallback entry can now name a DIFFERENT provider's
     endpoint+key for the same model, with a client-side stall timeout (not just 429 detection) as the
     failover trigger.
```
Full commit-by-commit detail: `git log --oneline dev`. `outputs/hcode_v2_rapport_context.md` and
`outputs/hcode-v2-final-audit.html` are useful history reads but were written before this arc — treat
their "what's next" sections as superseded by §6 below.

### What is blocked / known gaps

| Item | Status |
|---|---|
| D1: LangSmith tracing + eval set + defense runbook | Still blocked on a `gpt-oss` API key from enterprise IT — unchanged since the last refresh. See §1's free-tier-reality note for what dev/demo work runs on instead. |
| `main` promotion | Blocked on D1 |
| PR #123 (Cargo.lock + smoke-discovery doc rescue) | **OPEN, not merged** as of this refresh — verify before assuming either fix is live on `dev`. |
| Native desktop binary vs source | Rebuild + smoke-test before any native demo (Rule 4/10 — ask first); don't assume the last compiled binary matches HEAD. |
| CI has no UI/Rust coverage | `.github/workflows/ci.yml` runs two Python jobs only; the TypeScript UI and Rust desktop shell have no automated pipeline today — unchanged since the last refresh. |
| Live runs still show no diff card | Confirmed still true this refresh (`bridge.py` has no `file_patch` emission) — the daemon's real streaming path doesn't map file-tool output to a `file_patch` event; only the mock path does. |
| Verify phase / LSP self-correction is classifier-gated on live runs | New finding this refresh, see §9's PEV section — Verify (and its LSP diagnostics injection) only runs for tasks the (keyword-based) classifier calls "complex," or when the mode toggle (#118) forces it. An ordinary task in "fast" mode never reaches Verify. |
| VSCode extension | Out of scope for this project |

---

## 3. Architecture Quick Reference

```
Browser / Desktop UI (React, desktop-app/src-ui)
  ↕  VITE_MOCK=true      → mock fixtures (no daemon, CI-safe, offline demo)
  ↕  VITE_WS_URL=ws://…  → WebSocket proxy (dev-launcher / Docker frontend)
  ↕  otherwise           → Tauri invoke/listen (native desktop app)

scripts/ws_proxy.py (dev launcher / Docker) or desktop-app/src-tauri (native)
  ↕ stdio JSON-RPC
hcode_v2.daemon.server.JsonRpcDaemon
  ↕ astream_events (version="v2")
hcode_v2.daemon.bridge.StreamingBridge
  ↕
deepagents create_deep_agent(model, tools, middleware=[...], backend, checkpointer, system_prompt)
  middleware, IN ORDER as actually wired in factory.py today (order matters — later entries
  see earlier ones' prompt text; verified against source, not the previous version of this doc):
  ├── PEVMiddleware(diagnostics_provider=verify_diagnostics_addendum)   plan → execute → verify
  ├── PlanReviewMiddleware        opt-in (plan_review=True) — pauses at plan→execute for accept/reject.
  │                               FIXED in #117: shipped broken in #111 (see §7), now reliably re-armed
  │                               per task. Requires the run to actually reach the "plan" phase — see
  │                               ForcePlan below and the classifier-gate gap noted in §2/§9.
  ├── ForcePlanMiddleware         opt-in (force_plan=True) — the honest-mode-toggle fix (#118): forces
  │                               the full plan→execute→verify arc for any task, overriding the
  │                               classifier. Wired from the composer's "Plan" button, which used to
  │                               do nothing (params["mode"] was silently ignored pre-#118).
  ├── SafetyGuardMiddleware       backup write/edit/multi_edit/bash
  ├── SelectiveSkillsMiddleware  or  HCodeSkillsMiddleware               allowlist vs. full .hcode/skills/
  ├── WorkflowMiddleware                                                .hcode/workflows/
  ├── _ToolExclusionMiddleware                                          strips duplicate/unavailable tools
  ├── HarnessNotesMiddleware                                            one authoritative path/tool-naming note
  ├── PostEditLspMiddleware       on unless HCODE_POST_EDIT_LSP disables it (#119) — type-check safety
  │                               net after every write/edit, independent of PEV phase (fires even in
  │                               "fast" mode, unlike PEV's own Verify-phase LSP injection).
  ├── _PathContainmentMiddleware  re-homes any file-tool path under root_dir
  └── PromptSlimMiddleware        on unless HCODE_SLIM_PROMPT=0 (#121) — default level "1" (phase-scopes
                                  skills, drops dead vendored prompt sections + an unused tool; quality-
                                  neutral). Level "max" additionally caps completion tokens per phase —
                                  the free-tier lane, opt-in, NOT the default.
  system_prompt = <env> block + <project_map> block (#116, merged — no longer "not yet merged")
  ↕
LangChain ChatModel (self-hosted gpt-oss / gpt-4o-mini / Anthropic / any OpenAI-compatible endpoint)
  wrapped, outermost first:
  + ResilientChatModel   opt-in (HCODE_FALLBACK_MODELS or HCODE_MODEL_FALLBACK=auto). Since #122, a
  │                      fallback entry may be "model@provider-alias" to use a DIFFERENT provider's
  │                      endpoint+key (HCODE_PROVIDER_<ALIAS>_BASE_URL/_API_KEY) for the same model —
  │                      cross-provider dual-homing, not just same-provider model-slug fallback. Failover
  │                      triggers on 429/5xx (retry-then-advance, unchanged since #104) OR on a
  │                      client-side stall timeout (HCODE_FALLBACK_TIMEOUT, default 90s) — added
  │                      because a real provider (Cerebras) was found to fail by hanging with ZERO
  │                      bytes and NO error, which a 429-only design would never detect.
  + JsonToolCallWrapper  (json/auto modes — plain-text tool-call fallback for limited endpoints)
```

**Daemon JSON-RPC methods (13, unchanged since the last refresh — verified against source):** `health`,
`run_task`, `run_workflow`, `list_skills`, `list_workflows`, `list_sessions`, `list_models`,
`list_mcp_servers`, `connect_mcp_server`, `disconnect_mcp_server`, `abort`, `resume_plan`, `shutdown`.

**Streaming events emitted (`bridge.py` + `server.py`):** `planning_started`, `streaming_chunk`,
`plan_created`, `execution_started`, `task_update` (tool + `lsp_verify:*` steps), `verification_started`,
`plan_review`, `plan_rejected`, `model_fallback` (now optionally carries a failover *reason* — "timed
out after Ns" vs "rate-limited" — since #122), `aborted`, `done`, `error`. `file_patch` and
`verification` are STILL emitted only by the mock path (`_mock_streaming_task`), confirmed unfixed
this refresh — see "known gaps" above.

**Agent cache (`server.py`):** one agent per `thread_id`, evicted and rebuilt when `work_dir`,
`active_skills`, the MCP config signature, `model`, `plan_review`, or `force_plan` changes between
tasks on the same thread (the last one added with #118). Zero-config-change runs reuse the cached
agent (persisted via a SQLite checkpointer, `.hcode/sessions/<thread_id>.db`).

**The mock/live boundary (KEY PATTERN — this is now a project-wide LESSON, see the new verification
rule in §5a, not just a UI quirk):**
`VITE_MOCK=true` correctly mocks agent events but CANNOT fake filesystem/OS actions.
When `isTauri` is true, `openFolder`, `listDirectory`, `readFile`, `writeFile` in `bridge.ts`
call `invoke()` directly — bypassing `mockInvoke` — even when `VITE_MOCK=true`.
This pattern recurs for any new native-OS feature. Follow Rule 11.

---

## 4. Env Var Reference

| Variable | Canonical | Fallback | Default |
|---|---|---|---|
| Model name | `HCODE_MODEL_NAME` | `HCODE_MODEL` | `gpt-4o-mini` |
| API key | `HCODE_MODEL_API_KEY` | `OPENAI_API_KEY` | — |
| Base URL | `HCODE_MODEL_BASE_URL` | `OPENAI_BASE_URL` | provider default |
| Tool-calling mode | `HCODE_TOOLCALL_MODE` | — | `native` |
| Anthropic key | `ANTHROPIC_API_KEY` | — | — |
| Max tokens | `HCODE_MAX_TOKENS` | — | `8000` |
| **Prompt slimming** *(new, #121)* | `HCODE_SLIM_PROMPT` | — | `1` (phase-scoped skills, quality-neutral); `max` = also cap completion tokens per phase (free-tier lane, e.g. Groq's 8k/request cap — see §1); `0` = off, byte-identical pre-#121 behavior |
| Slim per-phase token caps *(level `max` only)* | `HCODE_SLIM_MAXTOK_PLAN` / `_EXECUTE` / `_VERIFY` | — | `2048` / `1280` / `768` |
| 429 fallback (explicit) | `HCODE_FALLBACK_MODELS` | — | unset = feature off. Since #122: an entry may be `model@provider-alias` for cross-provider dual-homing. |
| 429 fallback (auto) | `HCODE_MODEL_FALLBACK` | — | `off`; `auto` derives the list from `list_models` |
| **Cross-provider entry endpoint** *(new, #122)* | `HCODE_PROVIDER_<ALIAS>_BASE_URL` / `_API_KEY` | — | required per alias used in a `model@alias` fallback entry; missing → that entry is skipped with a warning, never silently misauthenticated |
| Fallback retries per model | `HCODE_MODEL_MAX_RETRIES` | — | `2` |
| Fallback backoff base (s) | `HCODE_MODEL_BACKOFF_BASE` | — | `0.5` (doubles each retry) |
| **Fallback stall timeout** *(new, #122)* | `HCODE_FALLBACK_TIMEOUT` | — | `90` seconds — first-token budget per candidate; `0` disables. The trigger a 429-only design misses (see §1/§3). |
| **Post-edit LSP gate** *(new, #119)* | `HCODE_POST_EDIT_LSP` | — | on unless set to `0`/`false`/`off` |
| LangSmith tracing | `LANGSMITH_TRACING` | — | `false` |
| UI transport (mock) | `VITE_MOCK` | `window.HCODE_MOCK` (Docker runtime) | `true` |
| WS proxy URL | `VITE_WS_URL` | `window.HCODE_WS_URL` (Docker runtime) | unset (Tauri) |
| Daemon WS bind (Docker) | `HCODE_WS_HOST` / `HCODE_WS_PORT` | — | `0.0.0.0:8765` in the backend image |

`HCODE_TOOLCALL_MODE`:
- `native` — standard OpenAI function-calling (default)
- `json` — inject plain-text JSON instruction, parse text response
- `auto` — inject instruction, prefer native tool_calls if present, fall back to JSON

---

## 5. Rules (corrected)

7. **When in doubt about scope — stop and ask.** Especially for `libs/deepagents/` (vendored,
   third-party — but see §9's vendored-freeze section: "third-party" has not meant "never edited" in
   practice; it means "don't edit it without a deliberate, logged decision to").
12. **The shared agent core is a COORDINATION ZONE — flag before merging.** The file list has grown as
    the middleware stack has: `src/hcode_v2/agent/factory.py`, `src/hcode_v2/agent/plan_review.py`,
    `src/hcode_v2/agent/force_plan.py`, `src/hcode_v2/agent/post_edit_lsp.py`,
    `src/hcode_v2/agent/prompt_slim.py`, `src/hcode_v2/agent/project_map.py`,
    `src/hcode_v2/agent/mcp_env.py`, `src/hcode_v2/provider/resilient.py`, `src/hcode_v2/daemon/server.py`,
    `src/hcode_v2/daemon/bridge.py`, plus the vendored middleware they wrap
    (`libs/deepagents/deepagents/middleware/pev.py`, `safety_guard.py`, `workflows.py`, and
    `backends/local_shell.py` — see §9 for why these four are listed as "vendored" rather than
    "read-only reference"). Before changing or merging anything here: investigate, design-checkpoint,
    and **coordinate with the other track's owner first**. Keep new capabilities **optional /
    additive** — every one shipped so far has a "default-off/level-0/absent-parameter-means-unchanged-
    behavior" guarantee; preserve that pattern.
14. **`libs/deepagents/` vendoring policy — NEEDS A JOINT DECISION, see §9.** The previous version of
    this rule claimed the freeze at `b57a6d2` "has held for the entire project so far... without
    exception." **That claim is false** — verified via `git log`, 15 commits after `b57a6d2` have
    edited vendored files directly (§9 has the full list). The *intent* behind this rule (prefer
    wrapping over editing) is sound and mostly followed for HCode's own additions
    (`SelectiveSkillsMiddleware`, `HarnessNotesMiddleware`, `ResilientChatModel`, `PlanReviewMiddleware`,
    `ForcePlanMiddleware`, `PostEditLspMiddleware`, `PromptSlimMiddleware` are all wrap-not-edit) — but
    the vendored files THEMSELVES have continued to receive direct bugfixes throughout the project.
    Until we agree on one of the two options in §9, treat any further direct edit to a vendored file as
    needing the same explicit call-out this rule always intended, not as a violation of a freeze that
    in practice was never enforced.

## 5a. The verification rule this project learned the hard way

**"Done" means proven via native runtime or a raw JSON-RPC probe — never the mock.** This is not a
style preference; it is a direct response to a pattern that has now repeated **five times**: a
feature passed when exercised through the mock (or in-process/unit tests) and then failed when
actually run — native desktop, live daemon, or a real model:

1. `work_dir` forwarding — worked in mock, silently used the app's cwd natively until fixed.
2. Skills forwarding + the skills panel — same shape of bug, native-only.
3. The native Tauri query correlator — `list_skills`/`list_workflows`/`list_sessions`/MCP calls
   silently returned empty over the native transport while the mock and WS transports were fine.
4. Plan-review (#111 → #117) — the mock **unconditionally emits the pause event**, so it "passed"
   every mock-driven check while the real daemon never paused at all. Only a raw JSON-RPC probe
   against the real daemon caught it.
5. The native desktop delivery pass itself — source-complete ≠ binary-current ≠ installed-and-working;
   each of those needed its own live check (see the native-smoke docs once #123 lands).

Practical rule for any future change to the daemon, bridge, provider layer, or native shell: verify
with (a) a raw JSON-RPC probe against the real (non-mock) daemon, and/or (b) an actual native run,
before calling it done. Unit tests over hand-built state (like the pre-#117 plan-review tests, which
asserted `_maybe_review({"_pev_phase": "execute", ...})` directly rather than driving the classifier
that decides the phase) are useful for regression coverage but are NOT a substitute for this — they
can pass while the real phase-transition path never reaches that state at all.

---

## 6. What's Next

**The prior "Immediate" list from the last refresh is fully superseded — every item on it (land
project-map, plan-review fix, native rebuild) has landed as #116/#117/one of the native-delivery
commits.** Current actual next steps, verified against what's open right now:

```
1. Land PR #123 (Cargo.lock + smoke-discovery doc rescue) — currently OPEN. Low risk, no co-owned
   files, should be a quick review.
2. Decide the vendored-freeze policy (§9/§14): re-freeze at a new baseline commit, or adopt a formal
   patch-log process for the vendored files that keep needing direct fixes (pev.py especially).
3. Close the two still-open native-desktop gaps from the discovery doc (once #123 lands and it's
   readable): the diff-card/file_patch gap (bridge.py, real streaming path), and the installed-app-
   has-no-bundled-daemon gap (daemon.rs's own discovery order already checks for a bundled binary;
   nothing produces one yet).
4. Consider whether the classifier-gate finding in §2/§9 (Verify/LSP self-correction doesn't run on
   live "fast"-mode tasks) needs its own fix, now that #118's ForcePlan gives users a way to opt in
   task-by-task — decide if that's sufficient or if defaults should change.
5. Clean up or intentionally resolve the two orphaned local branches (feat/tauri-build,
   feat/ui-redesign) noted in §2 — neither is pushed; decide push-and-PR vs. delete.
```

### D1 (when the API key arrives, unchanged since the last refresh)
```
1. Get gpt-oss base_url + api_key + model name from enterprise IT
2. Fill .env: HCODE_MODEL_NAME, HCODE_MODEL_BASE_URL, HCODE_MODEL_API_KEY
3. uv run python scripts/probe_model.py  → determine HCODE_TOOLCALL_MODE
4. Test end-to-end: python scripts/dev.py --work-dir . → type a task → see result
5. Enable LangSmith: LANGSMITH_TRACING=true, LANGSMITH_API_KEY=...
6. Build deterministic eval set (5–10 tasks) → run → record pass rate
7. Write defense runbook
8. Promote dev → main
```

---

## 7. Feature Arc Summary

Condensed milestone view — see `git log --oneline dev` for the full 300-commit history, or
`outputs/hcode_v2_rapport_context.md` Appendix D for a narrative version (written before this
refresh's arc; treat its coverage of #117 onward as absent, not authoritative).

| Phase | What it delivers | State |
|---|---|---|
| Foundation (C1–C4) | JSON-RPC daemon, streaming bridge, React UI port, Tauri launcher shell | ✅ merged |
| Native desktop delivery | One-click Windows launcher, Tauri capability fixes, production installer (NSIS/MSI) | ✅ merged; verify binary-vs-source currency before any demo (Rule 4/10) |
| W3 — LSP self-correction | `src/hcode_v2/lsp/` client, 4 agent tools, wired into PEV Verify + a phase-independent post-edit gate (#119) | ✅ merged. **Correction this refresh:** the PEV-Verify wiring only runs when Verify is reached at all, which is classifier-gated (see §2/§9) — #119's post-edit gate is the part that fires unconditionally on every edit. |
| Selectable skills | Per-task skill allowlist across CLI (`--skills`), chat (`/skills`), and GUI (checkbox panel) | ✅ merged |
| MCP Phase 1 / 1.5 / 2 | Live server connections, real-world catalog corrections, secure OS-keychain token auth | ✅ merged |
| Abort | Stop button cancels a running task cleanly, including mid-retry-backoff | ✅ merged |
| Live model selection | Per-task model dropdown, filtered to free + tool-capable models | ✅ merged |
| 429 model fallback | `ResilientChatModel` — retry-with-backoff then sticky fallback to the next model | ✅ merged (#104); **extended in #122** to cross-provider entries + stall-timeout failover |
| Plan review (HITL) | Pause at the plan→execute boundary for human accept/reject | ⚠️→✅ **Corrected history:** merged in #111 but shipped broken — PEV's TaskClassifier routed non-keyword-complex tasks to "fast," which never transitions, so the pause was unreachable for ordinary tasks; `_plan_reviewed` was also never reset per task. The mock hid both bugs (it emits the pause unconditionally — see §5a). **Fixed in #117** (merged, not just "on a branch" as the previous refresh said): `PlanReviewMiddleware` now forces the phase and resets the flag per task; raw JSON-RPC-probe-verified live. Edit-the-plan still deferred. |
| Honest mode toggle | Composer Plan/Fast buttons | ⚠️→✅ **New finding, corrected in #118:** the buttons existed and looked functional but the daemon silently ignored `params["mode"]` — decorative UI. #118 wires `mode="planning"` to `ForcePlanMiddleware`, which actually forces the full arc. |
| Post-edit LSP gate | Type-check safety net after every write/edit, any PEV phase | ✅ merged (#119) — new since the last refresh |
| Daemon-failure visibility | Loud error pill + banner (with stderr tail) instead of a silent dead daemon | ✅ merged (#120) — new since the last refresh |
| Prompt slimming | `HCODE_SLIM_PROMPT` — fits the arc under free-tier per-request caps | ✅ merged (#121) — new since the last refresh; see §1 |
| Cross-provider fallback | Dual-home a model across providers, stall-timeout-triggered failover | ✅ merged (#122) — new since the last refresh; see §1/§3 |
| Harness audit fixes | Tool-feedback integrity, then path-convention/tool-hygiene unification | ✅ merged, both with dedicated regression-test additions |
| Project-map context | `<project_map>` block giving the Plan phase real file paths | ✅ **merged (#116)** — previous refresh said "reviewed, not yet merged"; corrected. |
| Cargo.lock + smoke-discovery doc rescue | Two things stranded on an unpushed branch | 🔄 **PR #123, OPEN** — see §2 |

---

## 8. Test Inventory

| Suite | Location | Count | Command |
|---|---|---|---|
| Root (daemon, provider, tools, harness, contracts, unit) | `tests/` | **580 collected** (579 passed, 1 skipped) on a clean checkout of `dev @ 18260d9` | `uv run --group dev pytest tests/ -v` |
| deepagents CI subset | `libs/deepagents/tests/unit_tests/` | see `.github/workflows/ci.yml` for exact paths | run alongside the root suite in CI |

**Local-checkout caveat (verify before trusting a "1 failed" locally):** a workspace that has ever had
the (never-committed) `verify-with-language-server` skill directory created under
`.hcode/skills/` will see `test_selective_skills.py::TestFactoryActiveSkills::test_all_builtins_load_with_none`
fail — it asserts exactly 8 built-in skills and finds 9. This is caused by an **untracked, workspace-
local directory** (`git ls-tree dev -- .hcode/skills/` shows exactly 8 tracked skill dirs, not 9,
and confirms `verify-with-language-server` was never committed to `dev` despite an earlier version of
the local doc describing it as newly added — that description was inaccurate). A fresh `git clone` +
`pytest` is clean at 579/579 (+1 pre-existing skip). If you see this failure, it's a local artifact,
not a `dev` regression — but also don't ship that directory as-is without deciding whether the skill
should actually be added (committed) or removed.

**Known coverage gap (unchanged since the last refresh):** CI runs Python jobs only. The TypeScript UI
(`desktop-app/src-ui`) and the Rust desktop shell (`desktop-app/src-tauri`, which does have its own
in-source `#[cfg(test)]` regression tests) are not wired into `ci.yml` today.

---

## 9. v2 Architecture Details

### Vendored dependency — corrected

**`libs/deepagents/`** — deepagents, imported at commit `569b64c` (2026-05-26) and last touched by an
intentional "freeze" commit-message at `b57a6d2` (2026-05-30, "fix(critical): correct tool name
mismatches in SafetyGuard + PEV verify phase"). **The previous version of the local doc claimed this
freeze "has held for the entire project so far... without exception" and was "unchanged since the
project began — verified still current." Both claims are false, and were not actually re-verified —
they were carried forward from an earlier refresh.** Real history, from `git log --follow`:

#### Vendored freeze — actual history (facts only, no blame)

15 commits after `b57a6d2` have edited vendored files directly, spanning 2026-05-31 → 2026-06-14
(about two weeks), across 4 files:

| Commit | Date | Author | What changed |
|---|---|---|---|
| `1beda8b` | 2026-05-31 | Mohamed Jarboui | `safety_guard.py`: added `multi_edit` to the destructive/file tool sets |
| `4754d4b` | 2026-06-10 | lakriim | `pev.py`: bind no tools during the plan phase |
| `0cfa20b` | 2026-06-10 | lakriim | `pev.py`: strengthen plan prompt to emit `PLAN COMPLETE` |
| `679124f` | 2026-06-11 | lakriim | `pev.py` + `workflows.py`: classify latest human message, not first-of-thread |
| `4f431c2` | 2026-06-11 | lakriim | `pev.py`: skip empty-content messages in the loop detector |
| `5d0fe79` | 2026-06-11 | lakriim | `pev.py`: count iterations per phase so multi-step tasks aren't truncated |
| `aa2154d` | 2026-06-11 | lakriim | `pev.py`: retry plan when the model emits no marker, instead of ending silently |
| `b9a64d5` | 2026-06-11 | lakriim | `pev.py`: strengthen verify prompt to emit a verdict, not attempt unavailable tools |
| `d33296d` | 2026-06-12 | lakriim | `pev.py`: whitelist `read_file` in the verify phase |
| `0739baa` | 2026-06-12 | lakriim | `pev.py`: retry markerless tool-less turns in execute and verify |
| `23b9527` | 2026-06-12 | lakriim | `pev.py`: synthesize an honest `ISSUES FOUND` status on markerless breaker exits |
| `d23853b` | 2026-06-12 | lakriim | `pev.py`: align verify reader whitelist with both runtime tool layers |
| `312a7ab` | 2026-06-12 | lakriim | `backends/local_shell.py`: decode subprocess output with `errors=replace` + force UTF-8 (Windows cp1252/cp1256 crash fix) |
| `2801cf8` | 2026-06-06 | Mohamed Jarboui | `pev.py`: wire LSP diagnostics into PEV Verify via an optional provider (W3.3) |
| `0d97860` | 2026-06-14 | lakriim | `pev.py`: enrich execute-phase prompt with working-context and reliability guidance |

Additional confirmed facts: `libs/deepagents/deepagents/_version.py` (the `__version__` string) has
not been touched since the initial import (`569b64c`) — so the vendored package still self-reports as
whatever version it was imported as, despite 15 commits of drift. `graph.py` and `task_classifier.py`
have had zero edits since import/creation — the freeze *has* held for those two files specifically.

**This needs a joint decision, not a unilateral doc fix:** either (a) treat the current `dev` state of
these four files as a new, intentional baseline and record a fresh freeze commit, or (b) adopt a
formal lightweight patch-log (e.g. a `VENDOR_PATCHES.md` next to `libs/deepagents/` listing each
direct edit and why wrapping wasn't viable) so future contributors have an honest record instead of a
policy that says it's being followed when it demonstrably has not been. **Flagging for Abdelkrim and
whoever owns the GUI/daemon/Rust track to decide together — not resolved by this doc.**

**Tool surface:** 35 first-party tools (`src/hcode_v2/tools/registry.py::get_all_tools` — 6 file, 7 git,
5 terminal, 3 web, 3 interactive, 2 diff, 2 todo, 3 notebook, 4 LSP-semantic), plus any tools an MCP
server exposes at runtime. Re-verified this refresh via direct import — count and category breakdown
unchanged since the last refresh.

**SafetyGuard sets** (keep in sync with `tests/test_tool_name_contracts.py`):
- `_DESTRUCTIVE_TOOLS = {"write", "edit", "multi_edit", "bash"}`
- `_FILE_TOOLS = {"write", "edit", "multi_edit"}`

**Built-in skills** (`.hcode/skills/`, install-relative resolution via `skills_path.py`) — **corrected
count: 8 tracked on `dev`, verified via `git ls-tree`:**

| Skill | Covers |
|---|---|
| `clean-code` | Readable, small, intention-named code |
| `code-review` | Structured Python review (correctness/style/security) |
| `concise-planning` | Atomic, verb-first, ordered plan steps |
| `error-handling-patterns` | Specific exceptions, context, no swallowing |
| `pytest-idioms` | Idiomatic pytest usage |
| `run-tests-before-done` | Run tests for real before claiming EXECUTION COMPLETE |
| `systematic-debugging` | Root-cause-first, one hypothesis at a time, stop-after-3 rule |
| `tdd-lite` | Tests alongside/before new functions |

A `verify-with-language-server` skill directory exists in some local checkouts under `.hcode/skills/`
but **was never committed** — see §8's local-checkout caveat. The previous version of the local doc
described it as a newly-added, shipped skill; that was inaccurate. If the LSP-proactive-check gap it
was meant to close is still considered worth closing, it needs an actual commit, not just a local
directory.

**v1 read-only refs** (use `git -C hcode show main:...`):
- Daemon: `desktop-app/hcode-daemon/run_daemon.py`
- Event contract: `desktop-app/src-ui/src/types/agent-events.ts`
- IPC bridge: `desktop-app/src-ui/src/ipc/bridge.ts`
