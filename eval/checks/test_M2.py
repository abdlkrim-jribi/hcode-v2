from inventory.pricing import apply_bulk_discount

def test_bulk_discount_applies_to_every_price():
    assert apply_bulk_discount([100.0, 200.0], 10.0) == [90.0, 180.0]

def test_bulk_discount_empty_list():
    assert apply_bulk_discount([], 10.0) == []
