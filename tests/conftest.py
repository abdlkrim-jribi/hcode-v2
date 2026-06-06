"""Shared pytest configuration for the root test suite."""

from __future__ import annotations


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires an external tool (e.g. a real language server); "
        "skipped automatically when the tool is unavailable",
    )
