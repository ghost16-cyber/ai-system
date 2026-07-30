from app.services.pricing import LineItem, line_total


def test_line_total_uses_quantity():
    assert line_total(LineItem(price=7, quantity=3)) == 21
