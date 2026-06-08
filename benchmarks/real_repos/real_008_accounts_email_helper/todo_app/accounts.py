from todo_app.helpers import normalize_email


def canonical_email(value: str) -> str:
    return normalize_email(value)
