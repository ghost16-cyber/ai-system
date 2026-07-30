from checkout import total_with_tax


def test_total_with_tax_uses_eight_percent():
    assert total_with_tax(100) == 108
