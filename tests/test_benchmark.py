from pathlib import Path

from backend.app.benchmark.test_output_parser import parse_pytest_output
from backend.app.benchmark.trace_compactor import compact_orchestrator_trace


def test_parse_pytest_output_extracts_counts_and_failures():
    output = """
    ____ test_add ____
    E   AssertionError: assert 4 == 5
    FAILED test_calculator.py::test_add - AssertionError
    1 failed, 2 passed in 0.12s
    """

    parsed = parse_pytest_output(output, exit_code=1)

    assert parsed["status"] == "failed"
    assert parsed["failed"] == 1
    assert parsed["passed"] == 2
    assert parsed["duration_seconds"] == 0.12
    assert "test_calculator.py::test_add" in parsed["failing_tests"]
    assert parsed["failing_test_file"] == "test_calculator.py"
    assert parsed["failing_test_name"] == "test_add"
    assert parsed["assertions"] == (
        {
            "assertion": "assert 4 == 5",
            "actual_hint": "4",
            "expected_hint": "5",
        },
    )
    assert "AssertionError" in parsed["error_types"]


def test_parse_pytest_output_extracts_stack_source_paths():
    output = """
    FAILED tests/test_ranges.py::test_sum_inclusive_small_range - AssertionError
    tests/test_ranges.py:5: AssertionError
    src/ranges.py:3: in sum_inclusive
    assert 3 == 6
    1 failed in 0.05s
    """

    parsed = parse_pytest_output(output, exit_code=1)

    assert parsed["failing_test_file"] == "tests/test_ranges.py"
    assert parsed["failing_test_name"] == "test_sum_inclusive_small_range"
    assert parsed["stack_source_paths"] == ("tests/test_ranges.py", "src/ranges.py")
    assert parsed["assertions"][0]["actual_hint"] == "3"
    assert parsed["assertions"][0]["expected_hint"] == "6"


def test_parse_pytest_output_marks_zero_exit_as_passed():
    parsed = parse_pytest_output("3 passed in 0.04s", exit_code=0)

    assert parsed["status"] == "passed"
    assert parsed["passed"] == 3
    assert parsed["failed"] == 0


def test_compact_orchestrator_trace_keeps_useful_fields_without_raw_content():
    trace = {
        "task_id": "task-1",
        "goal": "Fix tests",
        "status": "completed",
        "intent": "debug_error",
        "candidate_files": ["calculator.py"],
        "inspected_files": ["calculator.py"],
        "tool_history": [
            {
                "action": "read_file",
                "allowed": True,
                "success": True,
                "output": {
                    "path": "calculator.py",
                    "content": {"redacted": True, "length": 40},
                },
            },
            {
                "action": "run_tests",
                "allowed": True,
                "success": True,
                "output": {"status": "passed", "exit_code": 0},
            },
        ],
        "proposed_patch": {
            "path": "calculator.py",
            "old": {"redacted": True, "length": 16},
            "new": {"redacted": True, "length": 16},
            "reason": "demo",
        },
        "validation": {
            "tests": {"status": "passed", "exit_code": 0},
            "risk": {"label": "low", "reason": "small patch"},
            "syntax": {"valid": True, "path": "calculator.py"},
            "patch_scope": {"valid": True, "changed_line_budget": 1},
        },
    }

    compact = compact_orchestrator_trace(trace)

    assert compact["tool_actions"] == ["read_file", "run_tests"]
    assert compact["tool_history"][0]["path"] == "calculator.py"
    assert "content" not in str(compact)
    assert compact["proposed_patch"]["old_length"] == 16
    assert compact["validation"]["tests"]["status"] == "passed"
