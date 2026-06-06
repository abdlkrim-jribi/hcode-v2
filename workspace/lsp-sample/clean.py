"""A clean, fully-typed module — pyright should report ZERO diagnostics."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def greet(name: str) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}!"


def total(values: list[int]) -> int:
    """Return the sum of a list of integers."""
    result = 0
    for v in values:
        result += v
    return result


if __name__ == "__main__":
    print(add(2, 3))
    print(greet("world"))
    print(total([1, 2, 3]))
