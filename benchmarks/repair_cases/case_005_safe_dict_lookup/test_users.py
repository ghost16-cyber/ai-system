from users import get_role


def test_get_role_defaults_to_guest():
    assert get_role({}) == 'guest'
