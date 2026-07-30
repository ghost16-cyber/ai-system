from pricing import discounted_price


def test_discounted_price_uses_ten_percent_discount():
    assert discounted_price(100) == 90
