from validators import can_signup


def test_eighteen_year_old_can_signup():
    assert can_signup(18) is True
