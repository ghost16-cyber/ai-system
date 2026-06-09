from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


REQUIRED_FIELDS = {
    "case_id",
    "split",
    "prompt",
    "expected_bug_type",
    "expected_source_file",
    "expected_difficulty",
    "expected_patch_risk",
    "expected_failure_category",
    "should_apply_patch",
    "is_adversarial_stress",
    "training_weight",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {path}")

    rows: list[dict[str, Any]] = []

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"Line {line_number} must be a JSON object.")

        rows.append(item)

    return rows


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []

    case_ids = {str(row.get("case_id", "")) for row in rows}
    categories = {str(row.get("expected_failure_category", "")) for row in rows}

    missing_case_ids = sorted(EXPECTED_CASE_IDS - case_ids)
    unexpected_case_ids = sorted(case_ids - EXPECTED_CASE_IDS)

    missing_categories = sorted(EXPECTED_CATEGORIES - categories)
    unexpected_categories = sorted(categories - EXPECTED_CATEGORIES)

    if len(rows) != 5:
        errors.append(f"Expected 5 rows, found {len(rows)}.")

    if missing_case_ids:
        errors.append(f"Missing case IDs: {missing_case_ids}")

    if unexpected_case_ids:
        errors.append(f"Unexpected case IDs: {unexpected_case_ids}")

    if missing_categories:
        errors.append(f"Missing categories: {missing_categories}")

    if unexpected_categories:
        errors.append(f"Unexpected categories: {unexpected_categories}")

    for index, row in enumerate(rows):
        case_id = str(row.get("case_id", f"<row-{index}>"))

        missing_fields = sorted(REQUIRED_FIELDS - set(row.keys()))
        if missing_fields:
            errors.append(f"{case_id}: missing fields {missing_fields}")
            continue

        expected_category = str(row["expected_failure_category"])
        expected_bug_type = str(row["expected_bug_type"])

        if row["split"] != "phase7_stress":
            errors.append(f"{case_id}: split must be phase7_stress.")

        if expected_category not in EXPECTED_CATEGORIES:
            errors.append(f"{case_id}: invalid expected_failure_category={expected_category!r}")

        if expected_bug_type != expected_category:
            errors.append(
                f"{case_id}: expected_bug_type must match expected_failure_category."
            )

        if row["expected_source_file"] != "":
            errors.append(
                f"{case_id}: expected_source_file must be empty for adversarial stress rows."
            )

        if row["expected_difficulty"] != "hard":
            errors.append(f"{case_id}: expected_difficulty must be hard.")

        if row["expected_patch_risk"] != "high":
            errors.append(f"{case_id}: expected_patch_risk must be high.")

        if row["should_apply_patch"] != "false":
            errors.append(f"{case_id}: should_apply_patch must be false.")

        if row["is_adversarial_stress"] != "true":
            errors.append(f"{case_id}: is_adversarial_stress must be true.")

        try:
            weight = float(row["training_weight"])
        except ValueError:
            errors.append(f"{case_id}: training_weight must be numeric.")
        else:
            if weight < 1.0:
                errors.append(f"{case_id}: training_weight must be >= 1.0.")

        prompt = str(row["prompt"])
        if len(prompt.strip()) < 40:
            errors.append(f"{case_id}: prompt is too short.")

    return {
        "passed": not errors,
        "row_count": len(rows),
        "unique_case_count": len(case_ids),
        "case_ids": sorted(case_ids),
        "categories": sorted(categories),
        "missing_case_ids": missing_case_ids,
        "unexpected_case_ids": unexpected_case_ids,
        "missing_categories": missing_categories,
        "unexpected_categories": unexpected_categories,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate exported Phase 7 stress advisor training rows."
    )
    parser.add_argument(
        "--input",
        default="benchmarks/.runs/phase7_stress_advisor_training_rows.jsonl",
        help="Path to exported Phase 7 stress training JSONL.",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/.runs/phase7_stress_training_rows_validation_report.json",
        help="Path to write validation report JSON.",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    rows = load_jsonl(input_path)
    report = validate_rows(rows)

    report["input_path"] = str(input_path)
    report["phase"] = "phase7_stress_training_rows_validation"

    print(json.dumps(report, indent=2, sort_keys=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"\nPhase 7 stress training row validation report written to: {output_path}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
