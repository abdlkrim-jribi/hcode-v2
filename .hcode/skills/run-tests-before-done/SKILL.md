---
name: run-tests-before-done
description: While executing, run the tests with the execute tool and show real output before declaring execution complete
---

# Run Tests Before Done

Applies during the EXECUTION phase only (when tools are available and you are
making changes). It does not change any phase protocol or end markers.

## Rule

While executing, after writing or modifying code and its tests:

1. Run the tests with the execute tool, e.g.:
   - `python -m pytest <test_file_or_dir> -q`
   - `python -m unittest <module>`
2. Include the actual command output (pass/fail counts, errors) in your
   response before emitting EXECUTION COMPLETE.
3. If tests fail, fix the code and re-run until they pass - still within
   the execution phase.

## Never

- Never claim tests pass without showing real output from an actual run.
- Never write "tests should pass" or "tests are expected to pass" as a
  substitute for running them.

If the project has no test runner or tests cannot run in this environment,
say so explicitly instead of implying they passed.
