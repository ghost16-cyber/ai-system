from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class PytestSummary:
    status: str
    exit_code: int | None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float | None = None
    failing_tests: tuple[str, ...] = ()
    failing_test_file: str | None = None
    failing_test_name: str | None = None
    assertions: tuple[dict[str, str | None], ...] = ()
    stack_source_paths: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()
    output_tail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_pytest_output(
    output: str | None,
    *,
    exit_code: int | None = None,
    max_tail: int = 1200,
) -> dict[str, Any]:
    text = output or ""
    counts = {
        "passed": _count_summary(text, "passed"),
        "failed": _count_summary(text, "failed"),
        "errors": _count_summary(text, "error", "errors"),
        "skipped": _count_summary(text, "skipped"),
    }
    duration = _duration(text)
    failing_tests = tuple(dict.fromkeys(_failing_tests(text)))
    first_test_file, first_test_name = _split_test_id(failing_tests[0]) if failing_tests else (None, None)
    assertions = tuple(_assertions(text))
    stack_source_paths = tuple(dict.fromkeys(_stack_source_paths(text)))
    error_types = tuple(dict.fromkeys(_error_types(text)))

    if exit_code == 0 or (exit_code is None and counts["passed"] and not counts["failed"] and not counts["errors"]):
        status = "passed"
    elif counts["failed"] or counts["errors"] or exit_code not in (0, None):
        status = "failed"
    else:
        status = "unknown"

    return PytestSummary(
        status=status,
        exit_code=exit_code,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        skipped=counts["skipped"],
        duration_seconds=duration,
        failing_tests=failing_tests,
        failing_test_file=first_test_file,
        failing_test_name=first_test_name,
        assertions=assertions,
        stack_source_paths=stack_source_paths,
        error_types=error_types,
        output_tail=text[-max_tail:] if text else None,
    ).to_dict()


def _count_summary(text: str, *words: str) -> int:
    total = 0
    for word in words:
        for match in re.finditer(rf"(\d+)\s+{re.escape(word)}\b", text):
            total += int(match.group(1))
    return total


def _duration(text: str) -> float | None:
    matches = re.findall(r"in\s+([0-9]+(?:\.[0-9]+)?)s\b", text)
    if not matches:
        return None
    return float(matches[-1])


def _failing_tests(text: str) -> list[str]:
    tests: list[str] = []
    patterns = [
        r"FAILED\s+([^\s]+::[^\s]+)",
        r"____+\s+([A-Za-z_][\w:.\-/]*)\s+____+",
    ]
    for pattern in patterns:
        tests.extend(re.findall(pattern, text))
    return tests


def _split_test_id(test_id: str) -> tuple[str | None, str | None]:
    if "::" not in test_id:
        return None, test_id or None
    file_path, test_name = test_id.split("::", 1)
    return file_path or None, test_name or None


def _assertions(text: str) -> list[dict[str, str | None]]:
    assertions: list[dict[str, str | None]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if "assert " in stripped and not stripped.startswith("assert "):
            stripped = stripped[stripped.index("assert ") :]
        if not stripped.startswith("assert "):
            continue
        actual = expected = None
        match = re.search(r"assert\s+(.+?)\s*==\s*(.+)$", stripped)
        if match:
            actual = _clean_assert_value(match.group(1))
            expected = _clean_assert_value(match.group(2))
        assertions.append(
            {
                "assertion": stripped,
                "actual_hint": actual,
                "expected_hint": expected,
            }
        )
    return assertions


def _clean_assert_value(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\s+#.*$", "", value)
    return value[:120]


def _stack_source_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"([A-Za-z0-9_./\\-]+\.py):(\d+):", text):
        path = match.group(1).replace("\\", "/")
        if path.startswith("<"):
            continue
        paths.append(path)
    return paths


def _error_types(text: str) -> list[str]:
    return re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b", text)
