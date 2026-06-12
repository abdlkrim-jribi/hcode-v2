---
name: concise-planning
description: Structure implementation plans as atomic, verb-first, ordered steps with explicit scope and a validation step
---

# Concise Planning

When producing a plan, use this structure. This skill shapes the CONTENT of
the plan only; follow the active phase protocol for how to end your response.

## Plan Structure

1. **Approach** - 1-3 sentences: what you will do and why.
2. **Scope** - bullets for what is In and what is Out. Make reasonable
   assumptions for unknowns and state them in the plan.
3. **Steps** - 4-10 atomic, ordered steps.
4. **Validation** - at least one step that checks the work (run tests,
   exercise the change, inspect outputs).

## Step Guidelines

- **Atomic**: each step is a single logical unit of work.
- **Verb-first**: "Add ...", "Refactor ...", "Verify ...".
- **Concrete**: name the specific files, modules, or tools involved.
- **Ordered**: later steps may depend on earlier ones, never the reverse.

## Example Steps

```text
1. Read src/parser.py to map the current tokenizer interface.
2. Add a normalize() helper in src/parser.py.
3. Update tokenize() to call normalize() on raw input.
4. Add unit tests for normalize() covering empty and unicode input.
5. Run the test suite and fix any failures.
```

Keep plans short. A good plan fits on one screen.
