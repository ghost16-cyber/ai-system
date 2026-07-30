from dedupe import unique


def test_unique_preserves_order():
    assert unique(['a', 'b', 'a']) == ['a', 'b']
