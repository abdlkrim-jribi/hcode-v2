---
name: systematic-debugging
description: Find the root cause before fixing - one hypothesis, smallest change, re-check; after 3 failed fixes stop and re-plan
---

# Systematic Debugging

When something fails (a test, a command, unexpected behavior), find the root
cause before changing code. Symptom patches create new bugs.

## Process

1. **Read the full error first** - complete message and stack trace, not
   just the first line. It often contains the exact answer.
2. **Reproduce it.** Confirm what triggers the failure. Check what changed
   recently (your own edits first).
3. **Form ONE specific hypothesis**: "X fails because Y." Compare against
   similar working code in the project if available.
4. **Make the smallest change that tests the hypothesis.** One variable at
   a time; no bundled fixes or "while I'm here" improvements.
5. **Re-check.** Fixed: move on. Not fixed: form a NEW hypothesis from the
   new evidence - never stack another fix on top.

While executing, prefer to capture the bug in a failing test before fixing
it, then make that test pass.

## Stop Rule

After about 3 failed fix attempts, STOP. Repeated failures mean the approach
is wrong, not the details. Re-read the relevant code, question the chosen
design, and re-plan a different approach instead of attempting fix number 4.

Never apply a fix you cannot explain, and never change several things at
once to "see if it works".
