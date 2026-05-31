from invoice import LineItem, line_total


def test_line_total_multiplies_price_by_quantity():
    assert line_total(LineItem(price=5, quantity=3)) == 15
