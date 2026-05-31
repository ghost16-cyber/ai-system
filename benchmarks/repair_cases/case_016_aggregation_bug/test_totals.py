from totals import total


def test_total_sums_all_values():
    assert total([2, 3, 4]) == 9
