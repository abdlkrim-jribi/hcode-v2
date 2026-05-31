# Development guidelines for HCode v2

This document provides context for working on **HCode v2** — a thin CLI shell
(`src/hcode_v2/`) built on the DeepAgents harness, which is vendored under
`libs/deepagents/`. See `README.md` for what the project is and how to run it.

> These guidelines govern the HCode v2 shell. The vendored `libs/deepagents/`
> tree is a frozen upstream snapshot (see the "Vendored dependency" section of the
> README) — treat it as a third-party library, not as code to refactor casually.

## Development tools & commands

- `uv` — package installer, resolver, and environment manager (replaces pip/poetry)
- `ruff` — linter and formatter
- `ty` — static type checking (as available)

Local development uses an editable install of the vendored library via
`[tool.uv.sources]` in `pyproject.toml`.

```bash
# install everything (runtime + vendored deepagents + dev tools)
uv sync

# run the HCode v2 shell test suite (no network)
uv run --group dev pytest

# run a single test file
uv run --group dev pytest tests/test_cli.py
```

### Suppressing ruff lint rules

Prefer inline `# noqa: RULE` over `[tool.ruff.lint.per-file-ignores]` for individual exceptions. `per-file-ignores` silences a rule for the *entire* file — if you add it for one violation, all future violations of that rule in the same file are silently ignored. Inline `# noqa` is precise to the line, self-documenting, and keeps the safety net intact for the rest of the file. Add comments to justify silencing. If you can't make a good justification for the ignore, it is probably code smell and should be re-evaluated.

Reserve `per-file-ignores` for **categorical policy** that applies to a whole class of files (e.g., `"tests/**" = ["D1", "S101"]` — tests don't need docstrings, `assert` is expected). These are not exceptions; they are different rules for a different context.

### PR and commit titles

Follow **Conventional Commits**. **All titles must include a scope, with no exceptions** — e.g. `feat(cli): ...`, `fix(provider): ...`, `test(tools): ...`, `chore(deps): ...`. Pick a scope that names the area you touched (`cli`, `provider`, `agent`, `tools`, `daemon`, `deps`, `docs`).

- Start the text after `type(scope):` with a lowercase letter, unless the first word is a proper noun (e.g. `Azure`, `GitHub`, `OpenAI`) or a named entity (class, function, method, parameter, or variable name).
- Wrap named entities in backticks so they render as code. Proper nouns are left unadorned.
- Keep titles short and descriptive — save detail for the body.

Examples:

```txt
feat(cli): add `--workdir` flag to run and chat
fix(provider): honour `OPENAI_BASE_URL` for self-hosted endpoints
test(tools): cover the full tool registry
chore(deps): pin vendored deepagents
```

### PR descriptions

The description *is* the summary — do not add a `# Summary` header.

- When the PR closes an issue, lead with the closing keyword on its own line at the very top, followed by a horizontal rule and then the body:

  ```txt
  Closes #123

  ---

  <rest of description>
  ```

  Only `Closes`, `Fixes`, and `Resolves` auto-close the referenced issue on merge.

- Explain the *why*: the motivation and why this solution is the right one. Limit prose.
- Write for readers who may be unfamiliar with this area of the codebase. Avoid insider shorthand.
- Do **not** cite line numbers; they go stale as soon as the file changes.
- Reference the affected symbol, class, or subsystem by name rather than full file paths.
- Wrap class, function, method, parameter, and variable names in backticks.

## Core development principles

### Maintain stable public interfaces

CRITICAL: Always attempt to preserve function signatures, argument positions, and names for exported/public methods. Do not make breaking changes.

**Before making ANY changes to public APIs:**

- Check if the function/class is exported in `__init__.py`
- Look for existing usage patterns in tests and examples
- Use keyword-only arguments for new parameters: `*, new_param: str = "default"`

Ask: "Would this change break someone's code if they used it last week?"

### Code quality standards

All Python code MUST include type hints and return types.

```python title="Example"
def filter_unknown_users(users: list[str], known_users: set[str]) -> list[str]:
    """Single line description of the function.

    Any additional context about the function can go here.

    Args:
        users: List of user identifiers to filter.
        known_users: Set of known/valid user identifiers.

    Returns:
        List of users that are not in the `known_users` set.
    """
```

- Use descriptive, self-explanatory variable names.
- Follow existing patterns in the codebase you're modifying.
- Break up complex functions (>20 lines) into smaller, focused functions where it makes sense.
- Avoid the `Any` type where a concrete type will do.

### Testing requirements

Every new feature or bugfix MUST be covered by unit tests.

- Tests live in `tests/` and must make **no network calls**.
- We use `pytest` as the testing framework; check existing tests for examples.
- Do NOT add `@pytest.mark.asyncio` to async tests — `asyncio_mode = "auto"` is set in `pyproject.toml`, so pytest-asyncio discovers them automatically.
- Prefer testing actual implementation over mocks; do not duplicate logic into tests.

Ensure the following:

- Does the test suite fail if your new logic is broken?
- Edge cases and error conditions are tested.
- Tests are deterministic (no flaky tests).

### Security and risk assessment

- No `eval()`, `exec()`, or `pickle` on user-controlled input.
- Proper exception handling (no bare `except:`); use a `msg` variable for error messages.
- Remove unreachable/commented code before committing.
- Watch for race conditions or resource leaks (file handles, sockets, threads); ensure proper cleanup.
- Never commit secrets — the company model is configured via env vars in a git-ignored `.env`.

### Documentation standards

Use Google-style docstrings with an Args section for all public functions.

- Types go in function signatures, NOT in docstrings.
- Focus on "why" rather than "what" in descriptions.
- Document all parameters, return values, and exceptions.
- Ensure American English spelling (e.g., "behavior", not "behaviour").
- Use single backticks (`code`) for inline code references — not Sphinx double backticks.

#### Model references in docs and examples

Always use current, generally-available models when referencing LLMs in docstrings, examples, and default values. Outdated model names signal stale code. Look up current model IDs from each provider's official docs rather than relying on memorized names.

## Additional resources

- **DeepAgents documentation** (the harness HCode v2 is built on): https://docs.langchain.com/oss/python/deepagents/overview
- **Vendored library source:** `libs/deepagents/` (frozen — see the README).
