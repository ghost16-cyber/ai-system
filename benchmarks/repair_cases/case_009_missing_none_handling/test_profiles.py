from profiles import display_name


def test_missing_user_is_anonymous():
    assert display_name(None) == 'anonymous'
