import pytest
from inventory import ItemNotFound
from inventory.errors import ItemNotFound as DirectItemNotFound
from inventory.models import Item
from inventory.store import InventoryStore

def test_is_keyerror_subclass():
    assert issubclass(ItemNotFound, KeyError)
    assert ItemNotFound is DirectItemNotFound

def test_get_item_raises_item_not_found():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=4))
    with pytest.raises(ItemNotFound):
        s.get_item("NOPE")
