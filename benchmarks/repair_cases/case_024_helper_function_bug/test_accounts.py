from accounts import canonical_email


def test_canonical_email_lowercases():
    assert canonical_email(' USER@Example.COM ') == 'user@example.com'
