from app.state import is_ready


def test_state_ready():
    assert is_ready(5), 'state check failed'
