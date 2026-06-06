"""A module with a deliberate TYPE error — pyright should report reportReturnType.

Expected diagnostic (Error severity):
  get_count: Type "str" is not assignable to return type "int"  [reportReturnType]
Also expected on the last line:
  Type "str"/"int" mismatch via get_count() assigned to `count: int`.
"""

from __future__ import annotations


def get_count() -> int:
    # BUG: annotated to return int, but returns a str.
    return "not a number"


def double(n: int) -> int:
    return n * 2


# The error above propagates: get_count() is typed int, so this line itself is fine,
# but the function body is the real defect W3 diagnostics must catch.
count: int = get_count()
print(double(count))
