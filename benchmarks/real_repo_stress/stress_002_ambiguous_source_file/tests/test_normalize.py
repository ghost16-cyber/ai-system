from app.normalize import normalize


def test_normalize_lowercases():
    assert normalize(' Name ') == 'name'
