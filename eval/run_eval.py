"""HCode experimental evaluation harness — real daemon, real JSON-RPC, real measurements.

Runs every (task x configuration) pair against a REAL daemon subprocess over the
raw stdio JSON-RPC transport — never the mock (see docs/verification.md and the
project's verification rule). Each run resets the sandbox to a byte-identical
clean state, applies the task's setup (bug / type-error injection), drives the
agent, then applies an OBJECTIVE success check. Results append to
``eval/results.jsonl``.

Nothing here estimates or synthesises a number: every field is derived from the
actual event stream, the actual wall clock, or the actual checker exit status.

Configurations (the supervisor's ablation)
------------------------------------------
A  "Agent sans Verify"            mode=fast,     HCODE_POST_EDIT_LSP=0
B  "PEV sans la porte LSP post-   mode=planning, HCODE_POST_EDIT_LSP=0
    édition (LSP de la phase
    Verify conservé)"
C  "PEV + LSP (système complet)"  mode=planning, HCODE_POST_EDIT_LSP=1

**Why B is named that way (verified in the code, not assumed):**
``factory.py`` passes ``diagnostics_provider=verify_diagnostics_addendum`` to
``PEVMiddleware`` UNCONDITIONALLY whenever PEV is enabled — there is no
environment switch for it. ``HCODE_POST_EDIT_LSP`` gates ONLY
``PostEditLspMiddleware``. So configuration B removes the post-edit LSP gate but
KEEPS the Verify-phase LSP addendum. Calling B a clean "PEV sans LSP" would
overstate the ablation, so it is not called that.

Free-tier discipline
--------------------
Infrastructure failures (429 / 413 / timeouts / provider errors) are recorded
with ``outcome="infra"`` and are NOT counted as agent successes or failures —
a rate limit is not a capability measurement. They are retried on a later run
(the resume logic only treats success/failure as settled). After
``--max-consecutive-infra`` infra hits in a row the harness stops cleanly so it
can be resumed the next day.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
SANDBOX_TEMPLATE = EVAL_DIR / "sandbox"
CHECKS_DIR = EVAL_DIR / "checks"
WORK_ROOT = EVAL_DIR / ".work"
RESULTS = EVAL_DIR / "results.jsonl"
VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():  # POSIX layout
    VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"

_PYRIGHT = shutil.which("pyright")


def pyright_cmd(*args: str) -> list[str]:
    """Argv for pyright. On Windows the npm shim is a ``.CMD``, which
    ``CreateProcess`` cannot execute directly — it has to go through ``cmd /c``."""
    if not _PYRIGHT:
        return ["pyright", *args]
    if os.name == "nt" and _PYRIGHT.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", _PYRIGHT, *args]
    return [_PYRIGHT, *args]

# Reuse the daemon-spawning machinery from the maintained probe (#126).
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from probe_daemon import _spawn_daemon  # noqa: E402

CONFIGS: dict[str, dict] = {
    "A": {
        "label_fr": "Agent sans Verify",
        "label_en": "Agent without Verify",
        "mode": "fast",
        "post_edit_lsp": "0",
    },
    "B": {
        "label_fr": "PEV sans la porte LSP post-édition (LSP de la phase Verify conservé)",
        "label_en": "PEV without the post-edit LSP gate (Verify-phase LSP retained)",
        "mode": "planning",
        "post_edit_lsp": "0",
    },
    "C": {
        "label_fr": "PEV + LSP (système complet)",
        "label_en": "PEV + LSP (full system)",
        "mode": "planning",
        "post_edit_lsp": "1",
    },
}

# The evaluator's DECLARED expectation of which initial phase each task type
# ought to get. This is a stated convention, not a property of the system — it
# is what "classifier accuracy" is measured against, and is reported alongside
# the raw phase distribution so the reader can judge the mapping itself.
EXPECTED_PHASE: dict[str, str] = {
    "trivial": "trivial",
    "simple": "fast",
    "moderate": "plan",
    "complex": "plan",
    "bug": "plan",
    "semantic": "plan",
}

# Substrings that mark a failure as INFRASTRUCTURE, not agent capability.
_INFRA_SIGNS: tuple[tuple[str, str], ...] = (
    ("rate_limit", "infra:rate_limit"),
    ("rate limit", "infra:rate_limit"),
    ("429", "infra:rate_limit"),
    ("quota", "infra:rate_limit"),
    ("tokens per", "infra:rate_limit"),
    ("413", "infra:payload_too_large"),
    ("request too large", "infra:payload_too_large"),
    ("too large", "infra:payload_too_large"),
    ("context_length", "infra:payload_too_large"),
    ("timed out", "infra:timeout"),
    ("timeout", "infra:timeout"),
    ("connection", "infra:provider"),
    ("apiconnection", "infra:provider"),
    ("502", "infra:provider"),
    ("503", "infra:provider"),
    ("overloaded", "infra:provider"),
    ("service unavailable", "infra:provider"),
)


def classify_error(text: str) -> str | None:
    """Return an ``infra:*`` label when ``text`` looks like infrastructure."""
    low = (text or "").lower()
    for needle, label in _INFRA_SIGNS:
        if needle in low:
            return label
    return None


# ── Task model ────────────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    type: str
    prompt: str
    setup: list[dict] = field(default_factory=list)
    success_check: list[dict] = field(default_factory=list)


def load_tasks(path: Path | None = None) -> list[Task]:
    data = yaml.safe_load((path or EVAL_DIR / "tasks.yaml").read_text(encoding="utf-8"))
    return [Task(**t) for t in data["tasks"]]


# ── Sandbox lifecycle ─────────────────────────────────────────────────────────

def reset_sandbox(work_dir: Path) -> None:
    """Wipe ``work_dir`` and recreate it as a byte-identical copy of the template."""
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    shutil.copytree(
        SANDBOX_TEMPLATE, work_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def apply_setup(work_dir: Path, setup: list[dict]) -> None:
    """Apply bug / type-error injection. Fails LOUDLY if an anchor is missing —
    a silently-skipped injection would invalidate the whole task."""
    for step in setup or []:
        kind = step.get("kind")
        if kind != "replace":
            raise ValueError(f"unknown setup kind: {kind}")
        target = work_dir / step["file"]
        text = target.read_text(encoding="utf-8")
        needle = step["find"]
        if needle not in text:
            raise ValueError(
                f"setup anchor not found in {step['file']}: {needle!r} — "
                "the sandbox and tasks.yaml have drifted apart"
            )
        target.write_text(text.replace(needle, step["replace"], 1), encoding="utf-8")


# ── Success checking (objective, automatic) ───────────────────────────────────

def _run(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "checker timed out"


def check_success(work_dir: Path, checks: list[dict], answer: str) -> tuple[bool, list[dict]]:
    """Run every condition; ALL must pass. Returns (ok, per-condition detail)."""
    details: list[dict] = []
    ok_all = True

    for cond in checks:
        kind = cond["kind"]
        ok, note = False, ""

        if kind == "answer_contains":
            low = (answer or "").lower()
            hits = [w for w in cond["any"] if w.lower() in low]
            ok, note = bool(hits), f"matched={hits}"

        elif kind == "pytest":
            copied = []
            for rel in cond["files"]:
                src = EVAL_DIR / rel
                dst = work_dir / f"_check_{src.name}"
                shutil.copyfile(src, dst)
                copied.append(dst.name)
            code, out = _run([str(VENV_PY), "-m", "pytest", "-q", *copied], work_dir)
            ok, note = code == 0, out.strip().splitlines()[-1] if out.strip() else f"exit {code}"

        elif kind == "pytest_baseline":
            code, out = _run([str(VENV_PY), "-m", "pytest", "-q", "tests"], work_dir)
            ok, note = code == 0, out.strip().splitlines()[-1] if out.strip() else f"exit {code}"

        elif kind == "pyright_clean":
            code, out = _run(pyright_cmd("--outputjson"), work_dir, timeout=300)
            try:
                errs = json.loads(out)["summary"]["errorCount"]
                ok, note = errs == 0, f"errorCount={errs}"
            except Exception:
                ok, note = False, f"pyright unparseable (exit {code})"

        elif kind == "file_contains":
            target = work_dir / cond["file"]
            text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            wanted = cond.get("any") or cond.get("all") or []
            hits = [w for w in wanted if w in text]
            ok = bool(hits) if "any" in cond else len(hits) == len(wanted)
            note = f"matched={hits}"

        elif kind == "glob_contains":
            hits = []
            for p in work_dir.glob(cond["glob"]):
                t = p.read_text(encoding="utf-8", errors="replace")
                hits += [f"{p.name}:{w}" for w in cond["any"] if w in t]
            ok, note = bool(hits), f"matched={hits[:4]}"

        else:
            ok, note = False, f"unknown check kind {kind}"

        details.append({"kind": kind, "ok": ok, "note": note[:300]})
        ok_all = ok_all and ok

    return ok_all, details


# ── Metric extraction from the real event stream ──────────────────────────────

_PHASE_EVENTS = {
    "planning_started": "plan",
    "plan_created": "plan_done",
    "execution_started": "execute",
    "verification_started": "verify",
}


def extract_metrics(events: list[dict]) -> dict:
    """Derive per-run metrics from the daemon's real event stream.

    ``n_tool_calls`` and ``n_corrections`` are EXACT (one event each).
    ``n_llm_calls`` is derived from model-turn boundaries, because the bridge
    does not map ``on_chat_model_start/end`` (bridge.py: "not mapped in C2").
    A turn is counted when a streaming run begins, or when a tool-call batch
    begins without a preceding streamed text run. This extractor is validated
    against a scripted model of known turn count by ``--validate-extractor``.
    """
    n_tool = n_corr = n_llm = 0
    phases: list[str] = []
    prev = None
    text_parts: list[str] = []
    summary = ""

    for e in events:
        t = e.get("type")
        payload = e.get("payload") or {}
        step = payload.get("step", "") or ""

        if t == "streaming_chunk":
            kind = "chunk"
            text_parts.append(str(payload.get("content", "")))
        elif t == "task_update" and step.startswith("tool:"):
            kind = "tool"
            n_tool += 1
        elif t == "task_update" and step.startswith("tool_result:"):
            kind = "toolres"
        else:
            kind = "other"

        if kind == "chunk" and prev != "chunk":
            n_llm += 1
        if kind == "tool" and prev in (None, "toolres", "other"):
            n_llm += 1
        prev = kind

        if step == "lsp_verify:errors":
            n_corr += 1
        if t in _PHASE_EVENTS:
            ph = _PHASE_EVENTS[t]
            if ph not in phases:
                phases.append(ph)
        if t == "done":
            summary = str(payload.get("summary", "") or "")

    reached = [p for p in phases if p != "plan_done"]
    return {
        "n_llm_calls": n_llm,
        "n_tool_calls": n_tool,
        "n_corrections": n_corr,
        "phases_reached": reached,
        "final_phase": reached[-1] if reached else None,
        "answer": (summary + "\n" + "".join(text_parts)).strip(),
    }


# ── One run against the real daemon ───────────────────────────────────────────

def run_once(task: Task, config_key: str, timeout_s: float, verbose: bool = True) -> dict:
    cfg = CONFIGS[config_key]
    work_dir = WORK_ROOT / f"{task.id}__{config_key}"
    reset_sandbox(work_dir)
    apply_setup(work_dir, task.setup)

    env = {
        "HCODE_POST_EDIT_LSP": cfg["post_edit_lsp"],
        "HCODE_SLIM_PROMPT": os.getenv("HCODE_SLIM_PROMPT", "max"),
        "HCODE_ROOT_DIR": str(work_dir),
    }

    events: list[dict] = []
    err_text = ""
    proc, q = _spawn_daemon(env)
    sent = False
    t0 = time.time()
    deadline = t0 + timeout_s
    timed_out = False

    try:
        while True:
            if time.time() >= deadline:
                timed_out = True
                break
            try:
                line = q.get(timeout=2)
            except queue.Empty:
                if proc.poll() is not None:
                    err_text = f"daemon exited early (code {proc.returncode})"
                    break
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "ready" and not sent:
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": "1", "method": "run_task",
                    "params": {
                        "task": task.prompt,
                        "mode": cfg["mode"],
                        "autonomous": True,
                        "thread_id": f"eval-{task.id}-{config_key}-{int(t0)}",
                        "work_dir": str(work_dir),
                    },
                }) + "\n")
                proc.stdin.flush()
                sent = True
                continue

            t = msg.get("type")
            if t:
                events.append(msg)
                if verbose and t not in ("streaming_chunk",):
                    step = (msg.get("payload") or {}).get("step", "")
                    print(f"    {t}" + (f" ({step})" if step else ""), flush=True)
            if t == "error":
                err_text = json.dumps(msg.get("payload") or {})[:1500]
            if t in ("done", "error", "aborted"):
                break
    finally:
        try:
            proc.stdin.write('{"jsonrpc":"2.0","method":"shutdown"}\n')
            proc.stdin.flush()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    duration = round(time.time() - t0, 2)
    metrics = extract_metrics(events)

    if timed_out:
        err_text = err_text or f"harness timeout after {timeout_s}s"
    infra = classify_error(err_text) if err_text else None
    if timed_out and not infra:
        infra = "infra:timeout"

    if infra:
        outcome, success, details = "infra", False, []
    else:
        success, details = check_success(work_dir, task.success_check, metrics["answer"])
        outcome = "success" if success else "failure"

    return {
        "task": task.id,
        "type": task.type,
        "config": config_key,
        "config_label_fr": cfg["label_fr"],
        "mode": cfg["mode"],
        "post_edit_lsp": cfg["post_edit_lsp"],
        "outcome": outcome,
        "success": success,
        "duration_s": duration,
        "n_llm_calls": metrics["n_llm_calls"],
        "n_tool_calls": metrics["n_tool_calls"],
        "n_corrections": metrics["n_corrections"],
        "phases_reached": metrics["phases_reached"],
        "final_phase": metrics["final_phase"],
        "error_type": infra,
        "error_text": err_text[:500] if err_text else None,
        "check_detail": details,
        "n_events": len(events),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Classifier accuracy (offline — ZERO model calls) ──────────────────────────

def classifier_report(tasks: list[Task]) -> list[dict]:
    from deepagents.middleware.task_classifier import TaskClassifier
    clf = TaskClassifier()
    rows = []
    for t in tasks:
        chosen = clf.get_initial_phase(t.prompt)
        expected = EXPECTED_PHASE[t.type]
        rows.append({
            "task": t.id, "type": t.type,
            "classifier_phase": chosen, "expected_phase": expected,
            "agree": chosen == expected,
        })
    return rows


# ── Extractor validation (keyless — ZERO model calls) ─────────────────────────

def validate_extractor() -> bool:
    """Drive the REAL daemon with a scripted model of KNOWN turn count and check
    that extract_metrics recovers it. Keyless: no API key, no quota."""
    sys.path.insert(0, str(REPO_ROOT / "tests" / "helpers"))
    from scripted_model import full_arc_script, save_script  # noqa

    work_dir = WORK_ROOT / "_extractor_validation"
    reset_sandbox(work_dir)
    script = full_arc_script("inventory/pricing.py")
    # Point the scripted edit at a real anchor in the sandbox.
    script[1]["tool_calls"][0]["args"] = {
        "path": "inventory/pricing.py",
        "old_string": "TAX_RATE = 0.20",
        "new_string": "# tuned\nTAX_RATE = 0.20",
    }
    tmp = WORK_ROOT / "_script.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    save_script(script, tmp)

    proc, q = _spawn_daemon({
        "HCODE_FAKE_MODEL": str(tmp), "HCODE_ALLOW_FAKE": "1",
        "HCODE_ROOT_DIR": str(work_dir), "HCODE_POST_EDIT_LSP": "0",
    })
    events, sent, t0 = [], False, time.time()
    try:
        while time.time() - t0 < 120:
            try:
                line = q.get(timeout=2)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ready" and not sent:
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": "1", "method": "run_task",
                    "params": {"task": "tune the tax rate", "mode": "planning",
                               "autonomous": True, "thread_id": f"validate-{int(t0)}",
                               "work_dir": str(work_dir)},
                }) + "\n")
                proc.stdin.flush()
                sent = True
                continue
            if msg.get("type"):
                events.append(msg)
            if msg.get("type") in ("done", "error", "aborted"):
                break
    finally:
        try:
            proc.stdin.write('{"jsonrpc":"2.0","method":"shutdown"}\n')
            proc.stdin.flush()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    m = extract_metrics(events)
    expected_turns = len(script)  # 4 scripted turns = 4 model calls
    ok = m["n_llm_calls"] == expected_turns
    print(f"  ground truth (scripted turns) : {expected_turns}")
    print(f"  extractor n_llm_calls         : {m['n_llm_calls']}  -> {'MATCH' if ok else 'MISMATCH'}")
    print(f"  n_tool_calls                  : {m['n_tool_calls']} (expect 1)")
    print(f"  phases_reached                : {m['phases_reached']}")
    return ok


# ── Resume / persistence ──────────────────────────────────────────────────────

def load_results() -> list[dict]:
    if not RESULTS.exists():
        return []
    rows = []
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def settled_pairs(rows: list[dict]) -> set[tuple[str, str]]:
    """Pairs that need no re-run: only success/failure are settled. ``infra``
    rows are deliberately NOT settled — a rate limit is retried later."""
    return {(r["task"], r["config"]) for r in rows if r.get("outcome") in ("success", "failure")}


def append_result(row: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tasks", help="comma-separated task ids (e.g. S1,B1,E1)")
    ap.add_argument("--types", help="comma-separated task types")
    ap.add_argument("--configs", default="A,B,C", help="comma-separated config keys")
    ap.add_argument("--limit", type=int, help="stop after N runs this session")
    ap.add_argument("--throttle", type=float, default=4.0, help="seconds between runs")
    ap.add_argument("--timeout", type=float, default=300.0, help="per-run timeout (s)")
    ap.add_argument("--max-consecutive-infra", type=int, default=4,
                    help="stop cleanly after this many infra failures in a row")
    ap.add_argument("--classifier-only", action="store_true",
                    help="offline classifier accuracy (no model calls) and exit")
    ap.add_argument("--validate-extractor", action="store_true",
                    help="keyless validation of the metric extractor and exit")
    ap.add_argument("--dry-run", action="store_true", help="list the plan and exit")
    args = ap.parse_args()

    tasks = load_tasks()

    if args.classifier_only:
        rows = classifier_report(tasks)
        agree = sum(r["agree"] for r in rows)
        print(f"{'task':6} {'type':10} {'classifier':11} {'expected':10} agree")
        for r in rows:
            print(f"{r['task']:6} {r['type']:10} {r['classifier_phase']:11} "
                  f"{r['expected_phase']:10} {'yes' if r['agree'] else 'NO'}")
        print(f"\nclassifier agreement: {agree}/{len(rows)} = {agree/len(rows)*100:.1f}%")
        (EVAL_DIR / "classifier.json").write_text(
            json.dumps(rows, indent=2), encoding="utf-8")
        return 0

    if args.validate_extractor:
        print("Validating metric extractor against a scripted model (keyless)...")
        return 0 if validate_extractor() else 1

    if args.tasks:
        keep = {t.strip() for t in args.tasks.split(",")}
        tasks = [t for t in tasks if t.id in keep]
    if args.types:
        keep = {t.strip() for t in args.types.split(",")}
        tasks = [t for t in tasks if t.type in keep]
    configs = [c.strip() for c in args.configs.split(",")]

    done = settled_pairs(load_results())
    plan = [(t, c) for t in tasks for c in configs if (t.id, c) not in done]
    if args.limit:
        plan = plan[: args.limit]

    print(f"planned runs: {len(plan)}  (already settled: {len(done)})")
    if args.dry_run:
        for t, c in plan:
            print(f"  {t.id:4} [{t.type:9}] x {c}")
        return 0

    consecutive_infra = 0
    for i, (task, cfg_key) in enumerate(plan, 1):
        print(f"\n[{i}/{len(plan)}] {task.id} ({task.type}) x config {cfg_key} "
              f"— mode={CONFIGS[cfg_key]['mode']}, post_edit_lsp={CONFIGS[cfg_key]['post_edit_lsp']}")
        try:
            row = run_once(task, cfg_key, args.timeout)
        except Exception as exc:  # harness-level fault: record, keep going
            row = {
                "task": task.id, "type": task.type, "config": cfg_key,
                "outcome": "infra", "success": False, "error_type": "infra:harness",
                "error_text": f"{type(exc).__name__}: {exc}"[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        append_result(row)
        mark = {"success": "PASS", "failure": "FAIL", "infra": "INFRA"}[row["outcome"]]
        print(f"  -> {mark}  {row.get('duration_s', '?')}s  "
              f"llm={row.get('n_llm_calls')} tools={row.get('n_tool_calls')} "
              f"corr={row.get('n_corrections')} phases={row.get('phases_reached')}"
              + (f"  [{row.get('error_type')}]" if row.get("error_type") else ""))

        if row["outcome"] == "infra":
            consecutive_infra += 1
            if consecutive_infra >= args.max_consecutive_infra:
                print(f"\nStopping cleanly: {consecutive_infra} consecutive infrastructure "
                      f"failures (likely free-tier quota). Re-run the same command later — "
                      f"settled pairs are skipped, infra pairs are retried.")
                return 2
            time.sleep(args.throttle * 5)  # back off harder after infra
        else:
            consecutive_infra = 0
            time.sleep(args.throttle)

    print("\nall planned runs complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
