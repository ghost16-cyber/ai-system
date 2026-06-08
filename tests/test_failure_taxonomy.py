from __future__ import annotations

from backend.app.benchmark.failure_taxonomy import classify_failure, count_failure_categories


def test_failure_taxonomy_detects_missing_patch():
    assert classify_failure({"fixed": False, "proposed_patch": None}) == "no_patch_proposed"


def test_failure_taxonomy_detects_wrong_file_selection():
    case = {
        "fixed": False,
        "proposed_patch": {"path": "tests/test_math.py"},
        "patch_quality": "irrelevant",
        "patch_touched_expected_file": False,
        "patch_touched_unexpected_file": True,
    }

    assert classify_failure(case) == "wrong_file_selected"


def test_failure_taxonomy_detects_failed_tests_after_patch():
    case = {
        "fixed": False,
        "proposed_patch": {"path": "src/math.py"},
        "patch_quality": "risky",
        "patch_touched_expected_file": True,
        "patch_touched_unexpected_file": False,
        "patch_applied": True,
        "tests_rerun_after_patch": True,
        "tests_passed_after_patch": False,
    }

    assert classify_failure(case) == "tests_failed_after_patch"


def test_failure_taxonomy_counts_categories():
    cases = [
        {"fixed": False, "proposed_patch": None},
        {"fixed": False, "proposed_patch": None},
        {"fixed": True},
    ]

    assert count_failure_categories(cases) == {"no_patch_proposed": 2}
