from inventory.pricing import apply_discount

def test_normal_discount_unchanged():
    assert apply_discount(100.0, 10.0) == 90.0

def test_negative_percent_leaves_price_unchanged():
    assert apply_discount(100.0, -25.0) == 100.0

def test_percent_above_100_gives_zero():
    assert apply_discount(100.0, 150.0) == 0.0
