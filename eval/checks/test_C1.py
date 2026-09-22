from inventory.models import Item
from inventory.report import format_by_category
from inventory.store import InventoryStore

def _store():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=4, category="tools"))
    s.add_item(Item(sku="B2", name="Gadget", unit_price=10.0, quantity=2, category="toys"))
    s.add_item(Item(sku="C3", name="Thing", unit_price=1.0, quantity=1))
    return s

def test_default_category_is_general():
    assert Item(sku="X", name="X", unit_price=1.0).category == "general"

def test_items_by_category_groups():
    groups = _store().items_by_category()
    assert set(groups) == {"tools", "toys", "general"}
    assert [i.sku for i in groups["tools"]] == ["A1"]

def test_format_by_category_mentions_each_category():
    out = format_by_category(_store())
    assert "tools" in out and "toys" in out and "general" in out
