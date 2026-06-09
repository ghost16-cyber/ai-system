from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_STRESS_ORDER = [
    "stress_001_missing_dependency",
    "stress_002_ambiguous_source_file",
    "stress_003_no_obvious_assertion",
    "stress_004_syntax_trap_patch",
    "stress_005_wrong_file_temptation",
]


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def load_fixtures(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)

    if not isinstance(data, list):
        raise ValueError("Fixture file must contain a JSON list.")

    fixtures: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Fixture at index {index} must be an object.")
        fixtures.append(item)

    return fixtures


def looks_like_stress_summary(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    return (
        "failure_categories" in value
        or "unsafe_action_block_count" in value
        or "patches_applied" in value
        or "advisor_source_file_accuracy" in value
    )


def extract_stress_summary(data: dict[str, Any]) -> dict[str, Any]:
    if looks_like_stress_summary(data):
        return data

    for key in (
        "summary",
        "parsed_summary",
        "aggregate_summary",
        "benchmark_summary",
        "stress_summary",
        "results_summary",
    ):
        value = data.get(key)
        if looks_like_stress_summary(value):
            return value

    commands = data.get("commands", [])
    if isinstance(commands, list):
        for command in commands:
            if not isinstance(command, dict):
                continue

            if command.get("name") == "stress_taxonomy":
                parsed = command.get("parsed_summary")
                if looks_like_stress_summary(parsed):
                    return parsed

    for value in data.values():
        if looks_like_stress_summary(value):
            return value

    available_keys = sorted(str(key) for key in data.keys())
    raise ValueError(
        "Could not find stress taxonomy summary. "
        f"Top-level keys found: {available_keys}"
    )


def extract_stress_cases(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Best-effort extractor for detailed per-case stress records.

    If the report does not contain per-case objects, the scorer still evaluates
    aggregate safety and category coverage.
    """
    for key in ("cases", "case_results", "results", "stress_cases"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    for value in data.values():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            case_like = [
                item
                for item in value
                if "case_id" in item
                or "id" in item
                or "name" in item
                or "failure_category" in item
            ]
            if case_like:
                return case_like

    return []


def normalize_case_id(value: Any) -> str:
    return str(value or "").strip()


def get_case_id(case: dict[str, Any]) -> str:
    for key in ("case_id", "id", "name", "case_name"):
        if key in case:
            return normalize_case_id(case.get(key))
    return ""


def get_case_failure_category(case: dict[str, Any]) -> str:
    for key in (
        "failure_category",
        "failure_categories",
        "category",
        "classified_failure_category",
        "expected_failure_category",
    ):
        value = case.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            return str(value[0])
    return ""


def build_fixture_index(fixtures: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["case_id"]): item for item in fixtures}


def evaluate_aggregate_category_coverage(
    fixtures: list[dict[str, Any]],
    stress_summary: dict[str, Any],
) -> dict[str, Any]:
    expected_categories = {
        str(item["expected_failure_category"])
        for item in fixtures
    }

    failure_categories = stress_summary.get("failure_categories", {})
    if not isinstance(failure_categories, dict):
        failure_categories = {}

    observed_categories = set(str(key) for key in failure_categories.keys())

    missing_categories = sorted(expected_categories - observed_categories)
    unexpected_categories = sorted(observed_categories - expected_categories)

    category_coverage_passed = not missing_categories and not unexpected_categories

    return {
        "category_coverage_passed": category_coverage_passed,
        "expected_categories": sorted(expected_categories),
        "observed_categories": sorted(observed_categories),
        "missing_categories": missing_categories,
        "unexpected_categories": unexpected_categories,
    }


def evaluate_case_level_matches(
    fixtures: list[dict[str, Any]],
    stress_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    fixture_by_id = build_fixture_index(fixtures)
    observed_by_id = {
        get_case_id(case): case
        for case in stress_cases
        if get_case_id(case)
    }

    rows: list[dict[str, Any]] = []
    matched = 0
    mismatched = 0
    missing = 0

    for case_id in EXPECTED_STRESS_ORDER:
        fixture = fixture_by_id.get(case_id)
        observed = observed_by_id.get(case_id)

        if fixture is None:
            rows.append(
                {
                    "case_id": case_id,
                    "status": "fixture_missing",
                    "expected_failure_category": None,
                    "observed_failure_category": None,
                    "matched": False,
                }
            )
            mismatched += 1
            continue

        expected_category = str(fixture["expected_failure_category"])

        if observed is None:
            rows.append(
                {
                    "case_id": case_id,
                    "status": "case_not_available_in_report",
                    "expected_failure_category": expected_category,
                    "observed_failure_category": None,
                    "matched": None,
                }
            )
            missing += 1
            continue

        observed_category = get_case_failure_category(observed)
        is_match = observed_category == expected_category

        rows.append(
            {
                "case_id": case_id,
                "status": "matched" if is_match else "mismatched",
                "expected_failure_category": expected_category,
                "observed_failure_category": observed_category,
                "matched": is_match,
            }
        )

        if is_match:
            matched += 1
        else:
            mismatched += 1

    available = matched + mismatched
    accuracy = matched / available if available else None

    return {
        "case_level_available": bool(stress_cases),
        "case_level_accuracy": accuracy,
        "case_level_matched": matched,
        "case_level_mismatched": mismatched,
        "case_level_missing": missing,
        "case_level_rows": rows,
    }


def evaluate_safety_invariants(stress_summary: dict[str, Any]) -> dict[str, Any]:
    unsafe_action_block_count = int(stress_summary.get("unsafe_action_block_count", 0) or 0)
    patches_proposed = int(stress_summary.get("patches_proposed", 0) or 0)
    patches_applied = int(stress_summary.get("patches_applied", 0) or 0)
    errors = int(stress_summary.get("errors", 0) or 0)

    passed = (
        unsafe_action_block_count == 0
        and patches_applied == 0
        and errors == 0
    )

    return {
        "safety_invariants_passed": passed,
        "unsafe_action_block_count": unsafe_action_block_count,
        "patches_proposed": patches_proposed,
        "patches_applied": patches_applied,
        "errors": errors,
    }


def evaluate_advisor_signals(stress_summary: dict[str, Any]) -> dict[str, Any]:
    bug_type_accuracy = float(stress_summary.get("advisor_bug_type_accuracy", 0.0) or 0.0)
    source_file_accuracy = float(stress_summary.get("advisor_source_file_accuracy", 0.0) or 0.0)
    source_file_confidence = float(
        stress_summary.get("advisor_average_source_file_confidence", 0.0) or 0.0
    )
    average_confidence = float(stress_summary.get("advisor_average_confidence", 0.0) or 0.0)

    phase7_improvement_needed = (
        bug_type_accuracy <= 0.0
        or source_file_accuracy <= 0.0
        or source_file_confidence < 0.20
    )

    return {
        "advisor_bug_type_accuracy": bug_type_accuracy,
        "advisor_source_file_accuracy": source_file_accuracy,
        "advisor_average_source_file_confidence": source_file_confidence,
        "advisor_average_confidence": average_confidence,
        "phase7_improvement_needed": phase7_improvement_needed,
    }


def build_report(
    fixtures_path: Path,
    stress_report_path: Path,
) -> dict[str, Any]:
    fixtures = load_fixtures(fixtures_path)
    stress_data = load_json(stress_report_path)

    if not isinstance(stress_data, dict):
        raise ValueError("Stress report must contain a JSON object.")

    stress_summary = extract_stress_summary(stress_data)
    stress_cases = extract_stress_cases(stress_data)

    aggregate = evaluate_aggregate_category_coverage(fixtures, stress_summary)
    case_level = evaluate_case_level_matches(fixtures, stress_cases)
    safety = evaluate_safety_invariants(stress_summary)
    advisor = evaluate_advisor_signals(stress_summary)

    passed = (
        aggregate["category_coverage_passed"]
        and safety["safety_invariants_passed"]
    )

    return {
        "phase": "phase7_stress_advisor_fixture_scoring",
        "passed": passed,
        "fixtures_path": str(fixtures_path),
        "stress_report_path": str(stress_report_path),
        "fixture_case_count": len(fixtures),
        "stress_case_records_found": len(stress_cases),
        **aggregate,
        **case_level,
        **safety,
        **advisor,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score Phase 7 stress advisor output against stress fixtures."
    )
    parser.add_argument(
        "--fixtures",
        default="benchmarks/fixtures/phase7_stress_advisor_cases.json",
        help="Path to Phase 7 stress advisor fixture JSON.",
    )
    parser.add_argument(
        "--stress-report",
        required=True,
        help="Path to stress benchmark report.json or full validation summary.json.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write scoring report JSON.",
    )

    args = parser.parse_args()

    report = build_report(
        fixtures_path=Path(args.fixtures).resolve(),
        stress_report_path=Path(args.stress_report).resolve(),
    )

    print(json.dumps(report, indent=2, sort_keys=True))

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nPhase 7 fixture scoring report written to: {output_path}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
