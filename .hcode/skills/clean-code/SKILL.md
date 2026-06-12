---
name: clean-code
description: Write readable, maintainable code with intention-revealing names, small single-purpose functions, and minimal comments
---

# Clean Code

Apply these principles to all code you write or modify.

## Names

- Intention-revealing: `elapsed_days`, not `d`.
- Truthful: do not call a dict `account_list`.
- Pronounceable and searchable; no cryptic abbreviations.
- Classes are nouns (`Customer`); functions are verbs (`post_payment`).

## Functions

- Small, and doing exactly one thing at one level of abstraction.
- Descriptive names: `is_password_valid`, not `check`.
- Arguments: 0-2 ideally; 3+ needs strong justification (consider a
  dataclass or object).
- No hidden side effects: a function should not secretly mutate global
  or unrelated state.

## Comments

- Prefer expressing intent in code: extract `is_eligible_for_benefits()`
  instead of commenting a boolean expression.
- Comment only what code cannot say: rationale, constraints, non-obvious
  external behavior, TODOs with context.
- Never leave commented-out code; delete it.
- No redundant or noise comments restating the line below.

## Structure

- Declare variables near their use; keep related lines together.
- High-level logic first, details below (top-down narrative).
- Avoid deep chains like `a.get_b().get_c().do()`.
- Raise exceptions instead of returning error codes; avoid returning None
  when an empty collection or exception is clearer.
