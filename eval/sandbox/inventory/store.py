"""The in-memory inventory store."""

from __future__ import annotations

from inventory.models import Item


class InventoryStore:
    """Holds :class:`~inventory.models.Item` objects keyed by SKU."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add_item(self, item: Item) -> None:
        """Insert or replace an item."""
        self._items[item.sku] = item

    def get_item(self, sku: str) -> Item:
        """Return the item for ``sku``, raising ``KeyError`` when absent."""
        return self._items[sku]

    def remove_item(self, sku: str) -> None:
        """Remove ``sku`` from the store if present."""
        self._items.pop(sku, None)

    def all_items(self) -> list[Item]:
        """Every item currently held, in insertion order."""
        return list(self._items.values())

    def total_value(self) -> float:
        """Total value of all stock held."""
        return sum(item.line_value() for item in self._items.values())
