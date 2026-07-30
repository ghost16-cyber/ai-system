from sorting import sort_scores


def test_sort_scores_ascending():
    assert sort_scores([3, 1, 2]) == [1, 2, 3]
