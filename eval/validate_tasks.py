"""Offline validation of the task set — ZERO model calls, zero quota.

Proves the 24 tasks are well-formed BEFORE any live campaign burns requests:

* every ``setup`` anchor still matches the sandbox (no silent drift);
* every injected bug actually BREAKS the baseline suite (otherwise the task is
  trivially "already passing" and measures nothing);
* every injected type error is actually SEEN by pyright (same reason);
* the pristine sandbox is green (pytest) and clean (pyright);
* every referenced hidden check file exists.

Run this after any edit to tasks.yaml or the sandbox.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))
from run_eval import (  # noqa: E402
    VENV_PY, WORK_ROOT, apply_setup, load_tasks, pyright_cmd, reset_sandbox,
)


def _pytest_ok(d: Path) -> bool:
    p = subprocess.run([str(VENV_PY), "-m", "pytest", "-q", "tests"],
                       cwd=str(d), capture_output=True, text=True, timeout=180)
    return p.returncode == 0


def _pyright_errors(d: Path) -> int:
    p = subprocess.run(pyright_cmd("--outputjson"), cwd=str(d),
                       capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout)["summary"]["errorCount"]
    except Exception:
        return -1


def main() -> int:
    tasks = load_tasks()
    failures: list[str] = []

    pristine = WORK_ROOT / "_validate_pristine"
    reset_sandbox(pristine)
    base_green = _pytest_ok(pristine)
    base_errs = _pyright_errors(pristine)
    print(f"pristine sandbox : pytest {'green' if base_green else 'RED'}, "
          f"pyright errors={base_errs}")
    if not base_green:
        failures.append("pristine sandbox tests are not green")
    if base_errs != 0:
        failures.append(f"pristine sandbox is not pyright-clean ({base_errs} errors)")

    print(f"\n{'task':6} {'type':10} {'setup':6} {'effect':28} verdict")
    for t in tasks:
        d = WORK_ROOT / f"_validate_{t.id}"
        reset_sandbox(d)

        try:
            apply_setup(d, t.setup)
            setup_ok = "ok" if t.setup else "none"
        except ValueError as exc:
            failures.append(f"{t.id}: {exc}")
            print(f"{t.id:6} {t.type:10} {'ANCHOR':6} {'--':28} FAIL ({exc})")
            continue

        effect, verdict = "n/a (no injection)", "ok"
        if t.type == "bug":
            broke = not _pytest_ok(d)
            effect = "baseline suite fails" if broke else "baseline STILL PASSES"
            verdict = "ok" if broke else "FAIL"
            if not broke:
                failures.append(f"{t.id}: injected bug does not break the baseline suite")
        elif t.type == "semantic":
            errs = _pyright_errors(d)
            effect = f"pyright errors={errs}"
            verdict = "ok" if errs > 0 else "FAIL"
            if errs <= 0:
                failures.append(f"{t.id}: injected type error not detected by pyright")

        for cond in t.success_check:
            for rel in cond.get("files", []):
                if not (EVAL_DIR / rel).exists():
                    failures.append(f"{t.id}: missing check file {rel}")
                    verdict = "FAIL"

        print(f"{t.id:6} {t.type:10} {setup_ok:6} {effect:28} {verdict}")

    print()
    if failures:
        print(f"{len(failures)} PROBLEM(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"all {len(tasks)} tasks valid — safe to run the live campaign")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
