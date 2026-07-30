from app.pricing import discounted_price


def test_ten_percent_discount():
    assert discounted_price(100) == 90
