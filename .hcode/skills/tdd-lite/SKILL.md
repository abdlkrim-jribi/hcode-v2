---
name: tdd-lite
description: Write behavior-focused tests alongside or before every new function, covering happy path, errors, and edge cases
---

# TDD Lite

A lightweight test-first habit for new code.

## Rules

- Every new function or method gets at least one test, written alongside
  or before the implementation.
- Test behavior, not implementation: assert on what the function returns
  or does, not on its internal calls.
- Name tests after the expected behavior: `test_returns_empty_list_for_no_matches`.
- One behavior per test; keep assertions focused.

## Coverage Order

1. Happy path - the main intended use.
2. Error cases - invalid input, expected exceptions.
3. Edge cases - empty input, None, zero, boundaries, duplicates.

## Structure

Use Arrange-Act-Assert: set up data, call the code under test, verify the
outcome.

Keep production code minimal: write just enough to satisfy the tests, then
clean up names and duplication while tests stay green.
