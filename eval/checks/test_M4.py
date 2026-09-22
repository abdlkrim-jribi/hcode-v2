from inventory.models import Item, Order
from inventory.store import InventoryStore

def _store():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=100))
    s.add_item(Item(sku="B2", name="Gadget", unit_price=10.0, quantity=100))
    return s

def test_order_total():
    o = Order(order_id="O1")
    o.add_line("A1", 4)
    o.add_line("B2", 2)
    assert o.total(_store()) == 30.0

def test_empty_order_total_is_zero():
    assert Order(order_id="O2").total(_store()) == 0.0
