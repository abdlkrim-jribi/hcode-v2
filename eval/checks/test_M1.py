from inventory.models import Item
from inventory.store import InventoryStore

def _store():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=1))
    s.add_item(Item(sku="B2", name="Gadget", unit_price=10.0, quantity=9))
    s.add_item(Item(sku="C3", name="Doohickey", unit_price=1.0, quantity=20))
    return s

def test_low_stock_filters_and_preserves_order():
    skus = [i.sku for i in _store().low_stock(10)]
    assert skus == ["A1", "B2"]

def test_low_stock_strictly_below():
    assert [i.sku for i in _store().low_stock(1)] == []
