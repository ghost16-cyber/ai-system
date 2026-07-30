from collector import append_item


def test_append_item_does_not_share_state():
    assert append_item('a') == ['a']
    assert append_item('b') == ['b']
