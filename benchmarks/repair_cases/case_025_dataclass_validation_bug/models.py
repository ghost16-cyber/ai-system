from dataclasses import dataclass


@dataclass
class SignupRules:
    min_age: int = 21
