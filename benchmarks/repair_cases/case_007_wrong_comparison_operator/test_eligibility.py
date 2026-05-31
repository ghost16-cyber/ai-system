from eligibility import is_adult


def test_eighteen_is_adult():
    assert is_adult(18) is True
