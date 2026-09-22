import pytest
from inventory.models import Item
from inventory.store import InventoryStore

def _store():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=4))
    return s

def test_restock_increases_quantity():
    s = _store()
    s.restock("A1", 6)
    assert s.get_item("A1").quantity == 10

def test_restock_unknown_sku_raises():
    with pytest.raises(KeyError):
        _store().restock("NOPE", 1)
