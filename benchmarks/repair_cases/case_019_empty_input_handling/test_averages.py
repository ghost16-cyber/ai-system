from averages import average


def test_average_empty_is_zero():
    assert average([]) == 0
