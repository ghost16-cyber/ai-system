from matching import contains


def test_contains_ignores_case():
    assert contains('Hello World', 'hello') is True
