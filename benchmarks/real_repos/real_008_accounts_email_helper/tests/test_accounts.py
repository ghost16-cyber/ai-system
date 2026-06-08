from todo_app.accounts import canonical_email


def test_canonical_email_is_lowercase():
    assert canonical_email(' USER@Example.COM ') == 'user@example.com'
