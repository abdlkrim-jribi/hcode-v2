from inventory.models import Item
from inventory.report import format_discounted
from inventory.store import InventoryStore

def test_item_discounted_price():
    assert Item(sku="A1", name="W", unit_price=100.0, quantity=1).discounted_price(10.0) == 90.0

def test_format_discounted_mentions_items():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=100.0, quantity=1))
    out = format_discounted(s, 10.0)
    assert "A1" in out and "90.00" in out
