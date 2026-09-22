from inventory.models import Item
from inventory.report import summarize
from inventory.store import InventoryStore

def test_singular_for_one_item():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.0, quantity=1))
    out = summarize(s)
    assert "1 item," in out and "1 items" not in out

def test_plural_for_two_items():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.0, quantity=1))
    s.add_item(Item(sku="B2", name="Gadget", unit_price=3.0, quantity=1))
    assert "2 items" in summarize(s)
