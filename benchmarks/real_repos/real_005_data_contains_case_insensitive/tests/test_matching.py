from data_utils.matching import contains


def test_contains_ignores_case():
    assert contains('Hello World', 'world') is True
