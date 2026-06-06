"""A module with a deliberate SYNTAX error — the file does not parse.

Expected diagnostic (Error severity):
  Expected ":"  — the function signature below is missing its trailing colon.
This exercises the parse-failure path: a file that doesn't even compile must
still yield a clean diagnostic (not crash the LSP client).
"""

from __future__ import annotations


def broken(x: int) -> int   # BUG: missing ":" at the end of the signature
    return x + 1


print(broken(41))
