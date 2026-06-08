from app.greeting import greet
from tests.helper import EXPECTED_GREETING


def test_greet_lowercase():
    assert greet('palla') == EXPECTED_GREETING
