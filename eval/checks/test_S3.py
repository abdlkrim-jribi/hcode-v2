from inventory.models import Item
from inventory.store import InventoryStore

def _store():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=4))
    return s

def test_present_sku_returns_item():
    assert _store().get_item("A1").name == "Widget"

def test_missing_sku_returns_none():
    assert _store().get_item("NOPE") is None
