from todo_app.stats import count_items


def test_count_items():
    assert count_items(['a', 'b', 'c']) == 3
