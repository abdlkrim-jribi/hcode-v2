from inventory.models import Item
from inventory.store import InventoryStore


def _store() -> InventoryStore:
    store = InventoryStore()
    store.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=4))
    store.add_item(Item(sku="B2", name="Gadget", unit_price=10.0, quantity=2))
    return store


def test_add_and_get():
    store = _store()
    assert store.get_item("A1").name == "Widget"


def test_total_value():
    assert _store().total_value() == 30.0


def test_remove_item():
    store = _store()
    store.remove_item("A1")
    assert len(store.all_items()) == 1
