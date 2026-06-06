# LSP sample project (W3 test fixtures)

Tiny Python project for exercising the W3 LSP client / diagnostics against real, known cases.
Run a language server (pyright) over these and assert the diagnostics match.

| File | Expected diagnostics | Tests |
|---|---|---|
| `clean.py` | **none** (empty list) | the happy path — well-typed code yields zero diagnostics |
| `type_error.py` | 1 Error — `reportReturnType` (returns `str`, annotated `int`) | semantic type checking |
| `syntax_error.py` | 1 Error — `Expected ":"` (missing colon, file doesn't parse) | parse-failure path (client must not crash) |

Notes:
- Diagnostics arrive via the server PUSH `textDocument/publishDiagnostics` after `didOpen`.
- Severity 1 = Error. W3.3 maps Errors → `ISSUES FOUND` in PEV Verify (see `hcode-pev-internals.md`).
- These are local W3 fixtures under the agent's `workspace/` bind mount; not part of the app.
