---
name: pytest-idioms
description: Write tests with pytest idioms - plain asserts, fixtures, parametrize, pytest.raises, tmp_path, monkeypatch
---

# Pytest Idioms

When writing Python tests, prefer pytest style over unittest style.

## Idioms

- Plain `assert` statements, not `self.assertEqual` / `assertTrue`.
- Plain test functions, not `TestCase` classes with setUp/tearDown.
- Shared setup goes in fixtures (decorate with `@pytest.fixture`, accept as
  a test argument); reusable fixtures live in `conftest.py`.
- Tables of inputs/expected outputs use
  `@pytest.mark.parametrize("value,expected", [...])` instead of copy-pasted
  test functions or loops inside one test.
- Expected exceptions use `with pytest.raises(ValueError, match="...")`,
  asserting on the type and message.
- File and directory tests use the `tmp_path` fixture, never real paths or
  the current working directory.
- Patch environment variables, attributes, and globals with the
  `monkeypatch` fixture instead of manual save/restore.
- Tests must be independent: no shared mutable state, no required ordering.

While executing, run tests with `python -m pytest <path> -q` via the execute
tool and read the failure output carefully before changing code.
