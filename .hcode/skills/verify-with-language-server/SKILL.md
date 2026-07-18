---
name: verify-with-language-server
description: After writing or editing source code, check it with check_diagnostics before treating the change as done - catches real type/syntax errors, not style opinions
---

# Verify With Language Server

Applies during EXECUTION, after writing or editing a source file that a
language server can check (currently Python).

## Rule

After a `write` or `edit` that touches a source file:

1. Call `check_diagnostics(path)` on that file.
2. If it reports ERROR-severity diagnostics, treat them like a failing test:
   fix the real cause, then re-check the same file.
3. If it reports "not available" / "no language server configured", that is
   not a failure — proceed normally. The check is a bonus signal, not a
   requirement the environment must satisfy.

This is a proactive habit for the EXECUTION phase. It does not replace the
automatic language-server check the VERIFY phase already runs on every file
you touched this task — that one happens regardless of this skill. Catching a
type error immediately after introducing it, in the same tool round, is
cheaper than discovering it several steps later once more code depends on the
mistake.

## Never

- Never treat a clean `check_diagnostics` result as proof the code is
  correct — it catches type/syntax errors, not logic bugs or missing tests.
- Never skip a real ERROR diagnostic because "the tests will probably still
  pass" — fix it or explain in your response why it is a false positive.
