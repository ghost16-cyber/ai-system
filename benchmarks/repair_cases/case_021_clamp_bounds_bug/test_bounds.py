from bounds import clamp


def test_clamp_caps_high_value():
    assert clamp(10, 0, 5) == 5
