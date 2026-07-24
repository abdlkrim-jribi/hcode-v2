# Verification — "done" means proven, not simulated

## The rule

**"Done" means proven via native runtime or a raw JSON-RPC probe — never the
mock.** Unit tests over hand-built state are useful for regression coverage,
but they are not a substitute for this: they can pass while the real
phase-transition path never reaches that state at all.

This is not a style preference. It is a direct response to a pattern that
repeated **five separate times** in this project: a feature passed when
exercised through the mock (or in-process/unit tests) and then failed when
actually run — native desktop, live daemon, or a real model.

## Why the mock keeps hiding real bugs

This project's mocks (`server.py::_mock_streaming_task`, the frontend
fixtures) **simulate event outcomes** — they emit `plan_created`,
`file_patch`, `lsp_verify:*`, a `plan_review` pause, etc. directly, without
any of the machinery that's supposed to produce them actually running. A mock
demo can look completely correct while the real code path underneath it is
broken, absent, or has never been exercised at all.

## The five cases (rationale, not blame)

1. **`work_dir` forwarding** — worked correctly through the mock; silently
   used the app's own cwd instead of the opened folder when run natively,
   until fixed.
2. **Skills forwarding + the skills panel** — same shape of bug, native-only.
3. **The native Tauri query correlator** — `list_skills`/`list_workflows`/
   `list_sessions`/MCP calls silently returned empty over the native
   transport while the mock and WS transports were fine.
4. **Plan-review (#111 → #117)** — the mock **unconditionally emits the pause
   event**, so it "passed" every mock-driven check while the real daemon
   never paused at all for an ordinary task (PEV's classifier routed it to a
   phase that never transitions). Only a raw JSON-RPC probe against the real
   daemon caught it — and the unit tests that existed at the time made it
   worse: they asserted `_maybe_review({"_pev_phase": "execute", ...})`
   directly, hand-setting the exact state the real classifier never actually
   produced for that task.
5. **The native desktop delivery pass itself** — source-complete ≠
   binary-current ≠ installed-and-working. Each of those needed its own live
   check; a stale compiled binary sat unnoticed for weeks because nothing
   ever re-verified it against a fresh source checkout.

## What actually catches these: two tools

### 1. `tests/helpers/scripted_model.py` — the C8 pattern

A deterministic, keyless `ScriptedChatModel` (`BaseChatModel` subclass) that
replays a fixed sequence of responses instead of calling a real provider. It
is wired in at the ONE seam where a real API call would otherwise happen
(`factory._build_model`) — **everything else in the pipeline is real**: the
actual `create_hcode_agent` graph, the actual middleware stack (PEV's phase
transitions, `PlanReviewMiddleware`'s interrupt, `PromptSlimMiddleware`, the
post-edit LSP gate), the actual tools (a scripted `edit` really writes to
disk; a scripted `check_diagnostics` really runs pyright).

This is what makes it safe to run in CI: no API key, no network, no quota,
no flakiness — but a real bug in PEV's phase machinery, the plan-review
interrupt, or the event bridge still surfaces, because that code actually
executes.

**A concrete example of what this catches, found while building this very
infrastructure:** the first version of `ScriptedChatModel` implemented only
`_generate`/`_agenerate` (the non-streaming path). Every scenario still
*completed* — PEV's phase machinery operates on message content directly and
doesn't care how the model was called — but `plan_created`,
`execution_started`, and `verification_started` **silently never fired**,
because `bridge.py`'s marker-detection is wired only to
`on_chat_model_stream`, and `BaseChatModel.astream()` skips the token-callback
path entirely for a model that never implements `_stream`/`_astream`. Real
providers stream, so this had never been visible before. The fix
(`ScriptedChatModel._stream`, one chunk carrying the full content) is now
permanent — and it means any future change to `bridge.py`'s streaming-event
mapping gets exercised by every scenario run, not just by luck against a
live model that happens to stream.

Two ways to use it:

- **In-process (pytest)** — `fresh_scripted_model(script)` returns a model
  instance; monkeypatch `factory._build_model` to return it, then drive
  `create_hcode_agent(...).ainvoke(...)` directly. See
  `tests/test_post_edit_lsp_integration.py` and `tests/test_scripted_pev_arc.py`.
- **Cross-process (the real daemon)** — a script is written to a JSON file
  and the daemon subprocess is launched with `HCODE_FAKE_MODEL=<path>` +
  `HCODE_ALLOW_FAKE=1` (two vars, so this can never activate by accident).
  `factory._build_model` checks this before touching any provider config at
  all. This is what `scripts/probe_daemon.py` uses.

### 2. `scripts/probe_daemon.py` — the raw JSON-RPC probe, consolidated

The single maintained home for "spawn the real daemon, drive it over the real
stdio JSON-RPC transport, assert on the real event stream." It replaces three
earlier one-off scripts written ad hoc during separate investigations (a PEV
mode/marker probe, a multi-provider arc bake-off probe, a cross-provider
stall/failover probe) — their logic now lives on as **scenarios**, not
separate files.

```bash
uv run python scripts/probe_daemon.py --list                          # see all scenarios
uv run python scripts/probe_daemon.py fast-no-arc                     # run one
uv run python scripts/probe_daemon.py --all                           # run every built-in scenario
```

#### Scenario spec format

```python
@dataclass
class Scenario:
    name: str
    task: str
    mode: str = "fast"                    # "planning" | "fast"
    plan_review: bool = False
    model: str | None = None              # optional model override (live runs)
    resume_decision: bool | None = None   # auto-resume a plan_review pause with accept/reject
    fake_script: Script | None = None     # None = drive the daemon against the REAL configured
                                           # model (live/bake-off mode, opt-in); a script list =
                                           # keyless, the default for built-in scenarios
    extra_env: dict[str, str] = field(default_factory=dict)
    expected_events: list[str] = field(default_factory=list)   # required event TYPES, in order,
                                                                 # as a subsequence of the real stream
    timeout_s: float = 120.0
    description: str = ""
```

`expected_events` is checked as an **in-order subsequence**, not an exact
match — other event types (`streaming_chunk`, `task_update`) may appear
between the required ones. This is deliberately loose: wording or step-detail
changes shouldn't break the probe, but a missing or reordered phase
transition still will.

#### Writing a new scenario

Add an entry to the `SCENARIOS` dict in `scripts/probe_daemon.py`. Most new
scenarios need only `task`, `mode`, `fake_script` (reuse
`full_arc_script()`/`no_arc_script()` from `tests/helpers/scripted_model.py`,
or write a custom turn list), and `expected_events`. For a scenario that needs
a REAL provider (e.g. re-running a live bake-off), leave `fake_script=None`
and set `model`/rely on the environment's `.env` — this is the same mechanism
`arc_probe.py` used, just registered here instead of living as a throwaway
script.

#### The 4 built-in scenarios

| Scenario | Proves |
|---|---|
| `fast-no-arc` | An ordinary task with no arc forcing stays in "fast" — no PEV markers, no plan/verify round-trips. |
| `planning-full-arc` | `mode="planning"` forces the full arc: `PLAN COMPLETE` → a real edit → `EXECUTION COMPLETE` → `VERIFIED OK`, with the daemon emitting `plan_created`/`execution_started`/`verification_started` for real. |
| `plan-review-accept` | `plan_review=True` actually pauses at the plan→execute boundary (#117); accepting resumes into execute/verify and the task completes. |
| `plan-review-reject` | Rejecting stops cleanly — **zero** tool calls, the file provably untouched on disk. |

Cross-provider stall/failover (the old `failover_probe.py`'s scenario) is
**not** reimplemented as a probe scenario — `HCODE_FAKE_MODEL` is a
process-wide short-circuit that returns before the fallback-chain path even
runs, so it cannot exercise a real multi-provider chain keylessly. That shape
is already covered by `tests/test_cross_provider_fallback.py` (15 keyless
unit tests on `ResilientChatModel` itself) and PR #122's own live probe
transcript. Re-running it live is still possible via a custom `Scenario` with
`fake_script=None` and a real `HCODE_PROVIDER_*`/`HCODE_FALLBACK_*` chain.

## CI

`.github/workflows/ci.yml` has a `windows-verification` job (in addition to
the two pre-existing Python-only Ubuntu jobs) that runs, on `windows-latest`
— the platform this app actually ships on:

1. **The full pytest suite** (`uv run --group dev python -m pytest tests/`).
2. **Every built-in probe scenario** (`scripts/probe_daemon.py --all`) — a
   real daemon subprocess, real JSON-RPC transport, real event bridge, zero
   API key.
3. **`tsc -b`**, the full TypeScript type-check — not just `vite build`
   (which does not type-check by itself; a type error had merged once
   because nothing before this ran the full check).

## Running the probe yourself

```bash
cd hcode-v2
uv run python scripts/probe_daemon.py --list
uv run python scripts/probe_daemon.py --all
```

No `.env`, no API key, no network access required for the built-in
scenarios — they are the CI-safe, keyless form of this project's own
verification rule.
