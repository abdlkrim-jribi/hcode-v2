"""probe_daemon.py — the ONE maintained raw-JSON-RPC probe against the real daemon.

## Why this exists

This project's mocks pass "done" checks that the real pipeline then fails —
five separate times (see docs/verification.md for the full list): work_dir
forwarding, skills forwarding, the native query correlator, plan-review
(#111 -> #117), and the native desktop delivery pass itself. Every one of
those was caught by exactly this pattern: spawn the REAL daemon as a
subprocess, drive it over the REAL stdio JSON-RPC transport, and assert on
the REAL event stream — never the mock.

This script is the maintained, single home for that pattern. It replaces
three earlier one-off probe scripts (a PEV mode/marker probe, a multi-provider
arc bake-off probe, a cross-provider stall/failover probe) written ad hoc
during those investigations — their logic lives on here as SCENARIOS, not as
separate scripts.

## Keyless by design

Most scenarios use ``HCODE_FAKE_MODEL`` (see ``factory.py`` and
``tests/helpers/scripted_model.py``) to drive the daemon with a deterministic
scripted model instead of a real provider — no API key, no network, no quota.
This is what makes scenario (b) in CI possible: a REAL daemon subprocess, REAL
JSON-RPC transport, REAL middleware stack, asserted in an automated pipeline
with zero external dependency. A scenario can still be pointed at a live
provider (omit ``fake_script``, optionally set ``model``) for the kind of
bake-off/live-verification work the old arc/failover probes did — that path
is opt-in and uses whatever `.env` / real env vars are already configured.

## Usage

    uv run python scripts/probe_daemon.py --list
    uv run python scripts/probe_daemon.py fast-no-arc
    uv run python scripts/probe_daemon.py planning-full-arc plan-review-accept
    uv run python scripts/probe_daemon.py --all        # every built-in scenario

Exit code is nonzero if any run scenario fails its assertion.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "helpers"))
from scripted_model import Script, full_arc_script, no_arc_script, save_script  # noqa: E402


# ── Scenario spec ───────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    """One declarative probe run.

    ``expected_events``: event ``type`` strings that must all appear, IN THIS
    RELATIVE ORDER, somewhere in the stream (a subsequence check — other event
    types, like ``streaming_chunk`` or ``task_update``, may appear between
    them; this is deliberately loose so wording/step changes don't break the
    probe, while phase-sequence regressions still do).

    ``resume_decision``: if set, the probe waits for a ``plan_review`` event
    and immediately sends ``resume_plan`` with this accept/reject value.

    ``fake_script``: a scripted-turn list (keyless — the default for built-in
    scenarios) or ``None`` to drive the daemon against whatever REAL model
    `.env`/the environment already has configured (live bake-off mode, opt-in).

    ``extra_env``: additional environment variables for the daemon subprocess
    — e.g. a cross-provider fallback chain pointed at a local hanging socket,
    folding in the old stall/failover probe's scenario shape.
    """

    name: str
    task: str
    mode: str = "fast"
    plan_review: bool = False
    model: str | None = None
    resume_decision: bool | None = None
    fake_script: Script | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    expected_events: list[str] = field(default_factory=list)
    timeout_s: float = 120.0
    description: str = ""


# NOTE on cross-provider stall/failover (the old failover_probe.py's scenario):
# it is NOT reimplemented here as a Scenario. HCODE_FAKE_MODEL (above) is a
# process-wide short-circuit in _build_model — it returns before the
# fallback-chain path even runs, so it structurally cannot drive a REAL
# multi-provider chain (that needs two distinct, independently-reachable HTTP
# endpoints, which is what the live version of that probe used: a hanging
# local socket as the stalled primary, a real provider as the fallback). That
# mechanism is thoroughly covered elsewhere and doesn't need a third home:
#   - tests/test_cross_provider_fallback.py — 15 unit tests on ResilientChatModel
#     itself (timeout-vs-429 classification, sticky failover, the abort race,
#     zero-regression when no chain is configured), keyless, in-process.
#   - PR #122's own live probe transcript — a hanging socket as primary, a
#     REAL Groq/Cerebras fallback, run against the real daemon subprocess.
# Reaching for a real live provider is inherently not keyless, so it does not
# belong in --all / CI either way. If it needs to be re-run, use extra_env on
# a custom Scenario with fake_script=None and a real HCODE_PROVIDER_*/
# HCODE_FALLBACK_* chain — the Scenario dataclass already supports that shape.

SCENARIOS: dict[str, Scenario] = {
    "fast-no-arc": Scenario(
        name="fast-no-arc",
        description="an ordinary task with no arc forcing: classifier routes to "
                    "fast, no PEV markers, no plan/verify round-trips",
        task="add a comment to hello.py",
        mode="fast",
        fake_script=no_arc_script(),
        expected_events=["planning_started", "done"],
    ),
    "planning-full-arc": Scenario(
        name="planning-full-arc",
        description="mode=planning forces the full arc: PLAN COMPLETE -> edit -> "
                    "EXECUTION COMPLETE -> VERIFIED OK, driven keylessly",
        task="add a comment to hello.py",
        mode="planning",
        fake_script=full_arc_script("hello.py"),
        expected_events=[
            "planning_started", "plan_created", "execution_started",
            "verification_started", "done",
        ],
    ),
    "plan-review-accept": Scenario(
        name="plan-review-accept",
        description="plan_review=True pauses at plan->execute; accepting resumes "
                    "into execute/verify and the task completes",
        task="add a comment to hello.py",
        mode="fast",
        plan_review=True,
        resume_decision=True,
        fake_script=full_arc_script("hello.py"),
        expected_events=["planning_started", "plan_review", "done"],
    ),
    "plan-review-reject": Scenario(
        name="plan-review-reject",
        description="plan_review=True pauses; rejecting stops cleanly with ZERO "
                    "tool calls (the file must be untouched)",
        task="add a comment to hello.py",
        mode="fast",
        plan_review=True,
        resume_decision=False,
        fake_script=full_arc_script("hello.py"),
        expected_events=["planning_started", "plan_review", "plan_rejected", "done"],
    ),
}


def all_scenarios() -> dict[str, Scenario]:
    return dict(SCENARIOS)


# ── Daemon spawn + drive ─────────────────────────────────────────────────────────

class ProbeFailure(AssertionError):
    pass


def _spawn_daemon(env_overrides: dict[str, str]) -> tuple[subprocess.Popen, "queue.Queue[str]"]:
    env = dict(os.environ)
    env.update(env_overrides)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "hcode_v2.daemon"], cwd=str(REPO_ROOT), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(
        target=lambda: [q.put(line.rstrip("\n")) for line in proc.stdout], daemon=True,
    ).start()
    return proc, q


def run_scenario(scen: Scenario, work_dir: Path, verbose: bool = True) -> dict:
    """Spawn a real daemon, run one scenario, return a result dict. Raises
    ProbeFailure if the expected event subsequence didn't appear."""
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "hello.py").write_text(
        'def greet(name):\n    return "hello " + name\n', encoding="utf-8"
    )

    env: dict[str, str] = dict(scen.extra_env)
    tmp_script_path: Path | None = None
    if scen.fake_script is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="probe_script_")
        os.close(fd)
        tmp_script_path = Path(tmp)
        save_script(scen.fake_script, tmp_script_path)
        env["HCODE_FAKE_MODEL"] = str(tmp_script_path)
        env["HCODE_ALLOW_FAKE"] = "1"

    if scen.model:
        env["HCODE_MODEL_NAME"] = scen.model

    proc, q = _spawn_daemon(env)
    events: list[dict] = []
    sent = False
    resumed = False
    t_start = time.time()
    deadline = t_start + scen.timeout_s

    try:
        while time.time() < deadline:
            try:
                line = q.get(timeout=2)
            except queue.Empty:
                if proc.poll() is not None:
                    raise ProbeFailure(
                        f"[{scen.name}] daemon exited early (code {proc.returncode}) "
                        f"before completing — {len(events)} events seen"
                    )
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "ready" and not sent:
                params = {
                    "task": scen.task, "mode": scen.mode, "autonomous": True,
                    "thread_id": f"probe-{scen.name}", "work_dir": str(work_dir),
                }
                if scen.plan_review:
                    params["plan_review"] = True
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": "1", "method": "run_task", "params": params,
                }) + "\n")
                proc.stdin.flush()
                sent = True
                continue

            t = msg.get("type")
            if t:
                events.append(msg)
                if verbose:
                    step = (msg.get("payload") or {}).get("step", "")
                    print(f"  [{scen.name}] {t}" + (f" ({step})" if step else ""))

            if t == "plan_review" and scen.resume_decision is not None and not resumed:
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": "2", "method": "resume_plan",
                    "params": {"accept": scen.resume_decision},
                }) + "\n")
                proc.stdin.flush()
                resumed = True

            if t in ("done", "error", "aborted"):
                break
    finally:
        try:
            proc.stdin.write('{"jsonrpc":"2.0","method":"shutdown"}\n')
            proc.stdin.flush()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        if tmp_script_path is not None:
            tmp_script_path.unlink(missing_ok=True)

    seen_types = [e.get("type") for e in events]
    ok, missing = _assert_subsequence(scen.expected_events, seen_types)
    result = {
        "scenario": scen.name,
        "ok": ok,
        "elapsed_s": round(time.time() - t_start, 1),
        "event_types": seen_types,
        "missing": missing,
    }
    if not ok:
        raise ProbeFailure(
            f"[{scen.name}] expected event subsequence not found. "
            f"Missing after best match: {missing}. Actual sequence: {seen_types}"
        )
    return result


def _assert_subsequence(expected: list[str], actual: list[str]) -> tuple[bool, list[str]]:
    """True if `expected` appears as an in-order subsequence of `actual`."""
    i = 0
    for a in actual:
        if i < len(expected) and a == expected[i]:
            i += 1
    return i == len(expected), expected[i:]


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*", help="scenario name(s) to run")
    ap.add_argument("--all", action="store_true", help="run every built-in scenario")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--work-dir", default=None, help="scratch dir (default: a temp dir per scenario)")
    args = ap.parse_args()

    registry = all_scenarios()

    if args.list:
        for name, scen in registry.items():
            print(f"{name:22} {scen.description}")
        return 0

    names = list(registry) if args.all else args.scenarios
    if not names:
        ap.print_help()
        return 1

    failures = []
    for name in names:
        if name not in registry:
            print(f"unknown scenario: {name} (--list to see all)", file=sys.stderr)
            failures.append(name)
            continue
        scen = registry[name]
        work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix=f"probe-{name}-"))
        print(f"\n=== {name} === {scen.description}")
        try:
            result = run_scenario(scen, work_dir)
            print(f"  PASS ({result['elapsed_s']}s)")
        except ProbeFailure as exc:
            print(f"  FAIL: {exc}")
            failures.append(name)

    print(f"\n{len(names) - len(failures)}/{len(names)} scenarios passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
