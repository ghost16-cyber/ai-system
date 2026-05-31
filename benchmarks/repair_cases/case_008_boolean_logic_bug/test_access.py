from access import can_edit


def test_owner_or_admin_can_edit():
    assert can_edit(True, False) is True
    assert can_edit(False, True) is True
