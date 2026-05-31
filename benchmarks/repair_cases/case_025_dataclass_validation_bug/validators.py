from models import SignupRules


def can_signup(age: int, rules: SignupRules | None = None) -> bool:
    rules = rules or SignupRules()
    return age >= rules.min_age
