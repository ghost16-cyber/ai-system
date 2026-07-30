from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "case_id",
    "expected_failure_category",
    "expected_bug_type",
    "expected_source_file_hint",
    "expected_patch_risk",
    "should_apply_patch",
    "rationale",
}


EXPECTED_CASE_IDS = {
    "stress_001_missing_dependency",
    "stress_002_ambiguous_source_file",
    "stress_003_no_obvious_assertion",
    "stress_004_syntax_trap_patch",
    "stress_005_wrong_file_temptation",
}


EXPECTED_CATEGORIES = {
    "missing_dependency",
    "ambiguous_source_file",
    "no_obvious_assertion",
    "syntax_trap_or_invalid_patch_blocked",
    "wrong_file_blocked",
}


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError("Fixture file must contain a JSON list.")

    cases: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Fixture item at index {index} must be an object.")
        cases.append(item)

    return cases


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []

    case_ids = [case.get("case_id") for case in cases]
    unique_case_ids = set(case_ids)

    if len(cases) != 5:
        errors.append(f"Expected 5 stress advisor cases, found {len(cases)}.")

    missing_case_ids = sorted(EXPECTED_CASE_IDS - unique_case_ids)
    unexpected_case_ids = sorted(unique_case_ids - EXPECTED_CASE_IDS)

    if missing_case_ids:
        errors.append(f"Missing case IDs: {missing_case_ids}")

    if unexpected_case_ids:
        errors.append(f"Unexpected case IDs: {unexpected_case_ids}")

    for case in cases:
        case_id = case.get("case_id", "<unknown>")

        missing_fields = sorted(REQUIRED_FIELDS - set(case.keys()))
        if missing_fields:
            errors.append(f"{case_id}: missing fields {missing_fields}")

        category = case.get("expected_failure_category")
        bug_type = case.get("expected_bug_type")
        patch_risk = case.get("expected_patch_risk")
        should_apply_patch = case.get("should_apply_patch")
        rationale = case.get("rationale")

        if category not in EXPECTED_CATEGORIES:
            errors.append(f"{case_id}: invalid expected_failure_category={category!r}")

        if bug_type not in EXPECTED_CATEGORIES:
            errors.append(f"{case_id}: invalid expected_bug_type={bug_type!r}")

        if bug_type != category:
            errors.append(
                f"{case_id}: expected_bug_type must match expected_failure_category "
                f"for Phase 7B fixtures."
            )

        if patch_risk != "high":
            errors.append(f"{case_id}: expected_patch_risk must be 'high'.")

        if should_apply_patch is not False:
            errors.append(f"{case_id}: should_apply_patch must be false.")

        if not isinstance(rationale, str) or len(rationale.strip()) < 20:
            errors.append(f"{case_id}: rationale is missing or too short.")

    passed = not errors

    return {
        "passed": passed,
        "case_count": len(cases),
        "unique_case_count": len(unique_case_ids),
        "missing_case_ids": missing_case_ids,
        "unexpected_case_ids": unexpected_case_ids,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Phase 7 stress advisor fixture cases."
    )
    parser.add_argument(
        "--fixtures",
        default="benchmarks/fixtures/phase7_stress_advisor_cases.json",
        help="Path to Phase 7 stress advisor fixture JSON.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write fixture evaluation JSON.",
    )

    args = parser.parse_args()

    fixture_path = Path(args.fixtures).resolve()
    cases = load_cases(fixture_path)
    report = evaluate_cases(cases)

    report["fixture_path"] = str(fixture_path)

    print(json.dumps(report, indent=2, sort_keys=True))

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nPhase 7 fixture report written to: {output_path}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
