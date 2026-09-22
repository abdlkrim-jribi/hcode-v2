"""Core data structures for the inventory package."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Item:
    """A single stock-keeping unit held in the inventory."""

    sku: str
    name: str
    unit_price: float
    quantity: int = 0

    def line_value(self) -> float:
        """Total value of this item's stock (price times quantity)."""
        return self.unit_price * self.quantity


@dataclass
class Order:
    """A customer order referencing items by SKU."""

    order_id: str
    lines: dict[str, int] = field(default_factory=dict)

    def add_line(self, sku: str, quantity: int) -> None:
        """Add ``quantity`` of ``sku`` to this order."""
        self.lines[sku] = self.lines.get(sku, 0) + quantity
