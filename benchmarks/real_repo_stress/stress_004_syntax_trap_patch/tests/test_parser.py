from app.parser import parse_flag


def test_yes_is_true():
    assert parse_flag('yes') is True
