from inventory.pricing import apply_discount, compute_total, with_tax


def test_apply_discount():
    assert apply_discount(100.0, 10.0) == 90.0


def test_compute_total():
    assert compute_total([1.0, 2.0, 3.5]) == 6.5


def test_with_tax():
    assert round(with_tax(100.0), 2) == 120.0
