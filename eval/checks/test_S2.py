from inventory.pricing import compute_total

def test_positive_prices_unchanged():
    assert compute_total([1.0, 2.0, 3.5]) == 6.5

def test_negative_prices_treated_as_zero():
    assert compute_total([10.0, -5.0, 2.0]) == 12.0

def test_all_negative():
    assert compute_total([-1.0, -2.0]) == 0.0
