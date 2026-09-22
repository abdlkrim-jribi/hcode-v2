"""Pricing helpers: discounts, totals and tax."""

from __future__ import annotations

TAX_RATE = 0.20


def apply_discount(price: float, percent: float) -> float:
    """Return ``price`` reduced by ``percent`` percent."""
    return price * (1.0 - percent / 100.0)


def compute_total(prices: list[float]) -> float:
    """Sum a list of line prices."""
    total = 0.0
    for price in prices:
        total += price
    return total


def with_tax(amount: float) -> float:
    """Return ``amount`` including tax at :data:`TAX_RATE`."""
    return amount * (1.0 + TAX_RATE)
