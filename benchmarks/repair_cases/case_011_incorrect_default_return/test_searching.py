from searching import find_index


def test_missing_value_returns_negative_one():
    assert find_index(['a', 'b'], 'z') == -1
