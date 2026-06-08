from data_utils.parser import safe_int


def test_safe_int_returns_zero_for_bad_input():
    assert safe_int('oops') == 0
