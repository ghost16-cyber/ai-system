from slugs import make_slug


def test_make_slug_uses_hyphens():
    assert make_slug('Hello World') == 'hello-world'
