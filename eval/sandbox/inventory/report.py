"""Human-readable reporting over an inventory store."""

from __future__ import annotations

from inventory.store import InventoryStore


def format_report(store: InventoryStore) -> str:
    """Render one line per item plus a total line."""
    lines = []
    for item in store.all_items():
        lines.append(f"{item.sku} {item.name} x{item.quantity} = {item.line_value():.2f}")
    lines.append(f"TOTAL {store.total_value():.2f}")
    return "\n".join(lines)


def summarize(store: InventoryStore) -> str:
    """One-line summary: item count and total value."""
    return f"{len(store.all_items())} items, total {store.total_value():.2f}"
