from inventory.models import Item
from inventory.report import format_report
from inventory.store import InventoryStore

def _store():
    s = InventoryStore()
    s.add_item(Item(sku="A1", name="Widget", unit_price=2.5, quantity=4))
    s.add_item(Item(sku="B2", name="Gadget", unit_price=10.0, quantity=2))
    return s

def test_total_with_tax():
    assert round(_store().total_with_tax(), 2) == 36.0

def test_report_has_tax_line():
    assert "TOTAL WITH TAX 36.00" in format_report(_store())
