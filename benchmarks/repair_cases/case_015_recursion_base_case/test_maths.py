from maths import factorial


def test_factorial_zero():
    assert factorial(0) == 1


def test_factorial_three():
    assert factorial(3) == 6
