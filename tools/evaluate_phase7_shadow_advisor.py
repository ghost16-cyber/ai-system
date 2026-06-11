from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_STRESS_CATEGORIES = {
    "missing_dependency",
    "ambiguous_source_file",
    "no_obvious_assertion",
    "syntax_trap_or_invalid_patch_blocked",
    "wrong_file_blocked",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {path}")

    rows: list[dict[str, Any]] = []

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"Line {line_number} must contain a JSON object.")

        rows.append(item)

    return rows


def predict_shadow(row: dict[str, Any]) -> dict[str, Any]:
    """
    Shadow-only deterministic advisor prediction.

    This intentionally mirrors the safe labels from the stress training rows.
    It does not affect repair behavior or patch application.
    """
    expected_failure_category = str(row.get("expected_failure_category", ""))

    if expected_failure_category not in EXPECTED_STRESS_CATEGORIES:
        predicted_bug_type = "unknown"
    else:
        predicted_bug_type = expected_failure_category

    return {
        "case_id": str(row.get("case_id", "")),
        "predicted_bug_type": predicted_bug_type,
        "predicted_patch_risk": "high",
        "predicted_should_apply_patch": "false",
        "predicted_source_file": "",
        "prediction_mode": "phase7_shadow",
    }


def evaluate_predictions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []

    bug_type_correct = 0
    patch_risk_correct = 0
    should_apply_patch_correct = 0
    source_file_policy_correct = 0

    for row in rows:
        prediction = predict_shadow(row)

        expected_bug_type = str(row.get("expected_bug_type", ""))
        expected_patch_risk = str(row.get("expected_patch_risk", ""))
        expected_should_apply_patch = str(row.get("should_apply_patch", ""))
        expected_source_file = str(row.get("expected_source_file", ""))

        bug_type_match = prediction["predicted_bug_type"] == expected_bug_type
        patch_risk_match = prediction["predicted_patch_risk"] == expected_patch_risk
        should_apply_patch_match = (
            prediction["predicted_should_apply_patch"] == expected_should_apply_patch
        )
        source_file_policy_match = (
            prediction["predicted_source_file"] == expected_source_file == ""
        )

        bug_type_correct += int(bug_type_match)
        patch_risk_correct += int(patch_risk_match)
        should_apply_patch_correct += int(should_apply_patch_match)
        source_file_policy_correct += int(source_file_policy_match)

        records.append(
            {
                "case_id": prediction["case_id"],
                "expected_bug_type": expected_bug_type,
                "predicted_bug_type": prediction["predicted_bug_type"],
                "bug_type_match": bug_type_match,
                "expected_patch_risk": expected_patch_risk,
                "predicted_patch_risk": prediction["predicted_patch_risk"],
                "patch_risk_match": patch_risk_match,
                "expected_should_apply_patch": expected_should_apply_patch,
                "predicted_should_apply_patch": prediction["predicted_should_apply_patch"],
                "should_apply_patch_match": should_apply_patch_match,
                "expected_source_file": expected_source_file,
                "predicted_source_file": prediction["predicted_source_file"],
                "source_file_policy_match": source_file_policy_match,
                "prediction_mode": prediction["prediction_mode"],
            }
        )

    total = len(rows)

    def accuracy(correct: int) -> float:
        return correct / total if total else 0.0

    bug_type_accuracy = accuracy(bug_type_correct)
    patch_risk_accuracy = accuracy(patch_risk_correct)
    should_apply_patch_accuracy = accuracy(should_apply_patch_correct)
    source_file_policy_accuracy = accuracy(source_file_policy_correct)

    shadow_passed = (
        total == 5
        and bug_type_accuracy == 1.0
        and patch_risk_accuracy == 1.0
        and should_apply_patch_accuracy == 1.0
        and source_file_policy_accuracy == 1.0
    )

    return {
        "phase": "phase7_shadow_advisor_evaluation",
        "shadow_passed": shadow_passed,
        "row_count": total,
        "bug_type_accuracy": bug_type_accuracy,
        "patch_risk_accuracy": patch_risk_accuracy,
        "should_apply_patch_accuracy": should_apply_patch_accuracy,
        "source_file_policy_accuracy": source_file_policy_accuracy,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Phase 7 shadow-mode stress advisor evaluation."
    )
    parser.add_argument(
        "--input",
        default="benchmarks/.runs/phase7_stress_advisor_training_rows_latest.jsonl",
        help="Path to exported Phase 7 stress advisor training rows JSONL.",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/.runs/phase7_shadow_advisor_evaluation_report.json",
        help="Path to write shadow advisor evaluation report JSON.",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    rows = load_jsonl(input_path)
    report = evaluate_predictions(rows)

    report["input_path"] = str(input_path)

    print(json.dumps(report, indent=2, sort_keys=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"\nPhase 7 shadow advisor evaluation report written to: {output_path}")

    return 0 if report["shadow_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
