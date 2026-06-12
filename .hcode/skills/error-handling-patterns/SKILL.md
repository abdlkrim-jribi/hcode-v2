---
name: error-handling-patterns
description: Catch specific exceptions, never swallow errors, raise with context, clean up with context managers, validate at boundaries
---

# Error Handling Patterns

Apply these rules to all code you write or modify.

## Rules

- Catch SPECIFIC exceptions (`ValueError`, `KeyError`, `OSError`), never
  bare `except:` and never `except Exception:` unless re-raising or at a
  top-level boundary.
- Never silence errors: no `except: pass`, no catch-log-and-continue when
  the operation actually failed. If you cannot handle it, let it propagate.
- Raise with context: error messages include the offending value or state
  (`f"unknown status: {status!r}"`). Chain causes with
  `raise NewError(...) from exc`.
- Fail fast: validate inputs at function and module boundaries and raise
  immediately on bad data, instead of letting it corrupt state downstream.
- Use context managers (`with open(...)`, `with lock:`) for anything that
  must be released - files, connections, locks - so cleanup survives
  exceptions.
- Catch exceptions only where you can do something useful: handle, add
  context and re-raise, or convert to a clear domain error.
- Avoid returning None or error codes to signal failure when raising an
  exception is clearer for the caller.
