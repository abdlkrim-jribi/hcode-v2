# inventory-sandbox

Fixed sandbox project for the HCode experimental evaluation.

`inventory/` holds the package under test (`models`, `pricing`, `store`,
`report`); `tests/` holds its baseline test suite. Every evaluation run resets
this directory to a clean checkout of its own git repository, so each
task x configuration pair starts from a byte-identical state.

Do not edit by hand while an evaluation is running.
