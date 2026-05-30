from parser import parse_age


def test_parse_age_returns_int():
    assert parse_age('42') == 42
