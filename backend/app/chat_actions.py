from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedChatAction:
    action_type: str
    command_action: str
    title: str
    summary: str
    assignment_task: str
    expected_result: str


_TEST_COMMAND_PHRASES = frozenset(
    {
        "run the test",
        "run the tests",
        "run tests",
        "execute the tests",
        "test the project",
        "check the tests",
        "run pytest",
        "pytest",
        "run the test suite",
    }
)


def detect_chat_action(message: str) -> DetectedChatAction | None:
    """Recognize unambiguous direct actions before routing or model work."""
    normalized = re.sub(r"\s+", " ", message.strip().lower())
    normalized = re.sub(r"[.!?]+$", "", normalized).strip()
    if normalized.startswith("please "):
        normalized = normalized[7:].strip()
    if normalized not in _TEST_COMMAND_PHRASES:
        return None
    return DetectedChatAction(
        action_type="command",
        command_action="pytest",
        title="Run the project test suite",
        summary=(
            "This action runs the project tests and is not expected to modify source files."
        ),
        assignment_task="Run the project test suite requested through Chat.",
        expected_result="Pytest pass/fail totals and relevant failing-test output.",
    )


def recognized_test_command_phrases() -> tuple[str, ...]:
    return tuple(sorted(_TEST_COMMAND_PHRASES))
