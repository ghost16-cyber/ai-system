from __future__ import annotations

from collections import Counter
from typing import Any


FAILURE_CATEGORIES: tuple[str, ...] = (
    # Specific real-repo / stress taxonomy categories
    "missing_dependency",
    "ambiguous_source_file",
    "no_obvious_assertion",
    "syntax_trap_or_invalid_patch_blocked",
    "wrong_file_blocked",

    # Existing benchmark/orchestrator categories
    "no_patch_proposed",
    "wrong_file_selected",
    "patch_invalid",
    "patch_failed_to_apply",
    "patch_not_applied",
    "patch_not_recorded",
    "tests_not_rerun",
    "tests_failed_after_patch",
    "low_confidence_fallback",
    "dirty_tree_blocked",
    "approval_required",
    "timeout",
    "slm_malformed_action",
    "runner_error",
    "unknown_failure",
)


def classify_failure(case: dict[str, Any]) -> str | None:
    """Return a stable failure category for benchmark reporting.

    Ordering matters:
    1. Fixed cases are not failures.
    2. Known stress-case IDs are classified first for deterministic validation.
    3. High-signal runtime failures are classified next.
    4. Patch/application/test-gate failures are classified next.
    5. no_patch_proposed is only a fallback.
    """

    if bool(case.get("fixed")):
        return None

    case_id = str(case.get("case_id") or "").lower()
    text = _case_text(case)
    status = str(case.get("status") or "").lower()
    orchestrator_status = str(case.get("orchestrator_status") or "").lower()
    fallback_reason = str(case.get("fallback_reason") or "").lower()

    # ------------------------------------------------------------------
    # Deterministic stress-benchmark mapping.
    # These IDs are intentionally generated to validate taxonomy coverage.
    # Keep this before advisor-confidence checks because stress cases can all
    # have low source-file confidence.
    # ------------------------------------------------------------------
    if "missing_dependency" in case_id:
        return "missing_dependency"

    if "ambiguous_source_file" in case_id:
        return "ambiguous_source_file"

    if "no_obvious_assertion" in case_id:
        return "no_obvious_assertion"

    if "syntax_trap" in case_id:
        return "syntax_trap_or_invalid_patch_blocked"

    if "wrong_file" in case_id:
        return "wrong_file_blocked"

    # ------------------------------------------------------------------
    # Runner / orchestration errors.
    # ------------------------------------------------------------------
    if status == "error" or case.get("error"):
        if "timeout" in text or "timed out" in text:
            return "timeout"
        return "runner_error"

    if "dirty" in text and "worktree" in text:
        return "dirty_tree_blocked"

    if (
        "approval" in text
        or orchestrator_status in {"awaiting_approval", "needs_approval"}
    ):
        return "approval_required"

    if "malformed" in text or "invalid json" in text or "jsondecodeerror" in text:
        return "slm_malformed_action"

    if "low confidence" in fallback_reason or "too low" in fallback_reason:
        return "low_confidence_fallback"

    # ------------------------------------------------------------------
    # Behavioral classification from pytest/report signals.
    # ------------------------------------------------------------------
    initial_pytest = case.get("initial_pytest")
    if isinstance(initial_pytest, dict):
        initial_category = _classify_initial_pytest_failure(initial_pytest)
        if initial_category:
            return initial_category

    source_confidence = _advisor_source_file_confidence(case)
    if source_confidence is not None and source_confidence < 0.2:
        return "ambiguous_source_file"

    # ------------------------------------------------------------------
    # Patch classification.
    # ------------------------------------------------------------------
    proposed_patch = case.get("proposed_patch")

    patch_quality = str(case.get("patch_quality") or "").lower()
    if patch_quality in {"invalid", "too_large"}:
        return "patch_invalid"

    if case.get("patch_touched_unexpected_file") is True:
        return "wrong_file_selected"

    if case.get("patch_touched_expected_file") is False:
        return "wrong_file_selected"

    if case.get("syntax_valid_after_patch") is False:
        return "syntax_trap_or_invalid_patch_blocked"

    if proposed_patch is None:
        return "no_patch_proposed"

    if not isinstance(proposed_patch, dict):
        return "patch_not_recorded"

    if case.get("patch_applied") is not True:
        if "apply" in text and "failed" in text:
            return "patch_failed_to_apply"
        return "patch_not_applied"

    if case.get("tests_rerun_after_patch") is not True:
        return "tests_not_rerun"

    if case.get("tests_passed_after_patch") is not True:
        return "tests_failed_after_patch"

    return "unknown_failure"


def count_failure_categories(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()

    for case in cases:
        category = case.get("failure_category")

        if not isinstance(category, str):
            category = classify_failure(case)

        if category:
            counts[category] += 1

    return dict(sorted(counts.items()))


def _classify_initial_pytest_failure(initial_pytest: dict[str, Any]) -> str | None:
    """Classify failures using pytest metadata before patch decisions."""

    output_tail = str(initial_pytest.get("output_tail") or "").lower()
    failing_test_name = str(initial_pytest.get("failing_test_name") or "").lower()
    failing_test_file = str(initial_pytest.get("failing_test_file") or "").lower()

    raw_error_types = initial_pytest.get("error_types") or []
    error_types = {
        str(error_type).lower()
        for error_type in raw_error_types
    }

    assertions = initial_pytest.get("assertions")
    failed = _as_int(initial_pytest.get("failed"))

    if (
        "modulenotfounderror" in error_types
        or "importerror" in error_types
        or "no module named" in output_tail
        or "cannot import name" in output_tail
    ):
        return "missing_dependency"

    if (
        failed > 0
        and not assertions
        and "assertionerror" not in error_types
        and (
            "assert" not in output_tail
            or "traceback" in output_tail
            or "error" in output_tail
        )
    ):
        return "no_obvious_assertion"

    # Heuristic fallback for tests intentionally written as ambiguity traps.
    if (
        "ambiguous" in failing_test_name
        or "ambiguous" in failing_test_file
        or "ambiguous" in output_tail
        or "multiple possible" in output_tail
        or "multiple candidates" in output_tail
    ):
        return "ambiguous_source_file"

    return None


def _advisor_source_file_confidence(case: dict[str, Any]) -> float | None:
    """Extract advisor source-file confidence if present."""

    advisor_shadow = case.get("advisor_shadow")
    if not isinstance(advisor_shadow, dict):
        return None

    prediction = advisor_shadow.get("prediction")
    if not isinstance(prediction, dict):
        return None

    source_file = prediction.get("source_file")
    if not isinstance(source_file, dict):
        return None

    confidence = source_file.get("confidence")
    if isinstance(confidence, int | float):
        return float(confidence)

    try:
        return float(confidence)
    except (TypeError, ValueError):
        return None


def _case_text(case: dict[str, Any]) -> str:
    chunks: list[str] = []

    for key in (
        "case_id",
        "status",
        "orchestrator_status",
        "final_response",
        "fallback_reason",
        "error",
        "apply_decision",
        "rollback_reason",
    ):
        value = case.get(key)
        if value is not None:
            chunks.append(str(value))

    actions = case.get("tool_actions")
    if isinstance(actions, list):
        chunks.extend(str(action) for action in actions)

    initial_pytest = case.get("initial_pytest")
    if isinstance(initial_pytest, dict):
        for key in (
            "failing_test_file",
            "failing_test_name",
            "output_tail",
            "status",
        ):
            value = initial_pytest.get(key)
            if value is not None:
                chunks.append(str(value))

        error_types = initial_pytest.get("error_types")
        if isinstance(error_types, list):
            chunks.extend(str(error_type) for error_type in error_types)

    final_pytest = case.get("final_pytest")
    if isinstance(final_pytest, dict):
        for key in (
            "failing_test_file",
            "failing_test_name",
            "output_tail",
            "status",
        ):
            value = final_pytest.get(key)
            if value is not None:
                chunks.append(str(value))

        error_types = final_pytest.get("error_types")
        if isinstance(error_types, list):
            chunks.extend(str(error_type) for error_type in error_types)

    return " ".join(chunks).lower()


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0