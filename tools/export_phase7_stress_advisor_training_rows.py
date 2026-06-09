from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_FIXTURES = "benchmarks/fixtures/phase7_stress_advisor_cases.json"


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


def make_training_row(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case["case_id"])
    failure_category = str(case["expected_failure_category"])
    bug_type = str(case["expected_bug_type"])
    patch_risk = str(case["expected_patch_risk"])
    should_apply_patch = bool(case["should_apply_patch"])
    source_file_hint = case.get("expected_source_file_hint")
    rationale = str(case["rationale"])

    prompt = (
        f"Stress advisor case {case_id}. "
        f"Failure category: {failure_category}. "
        f"Rationale: {rationale}"
    )

    return {
        "case_id": case_id,
        "split": "phase7_stress",
        "prompt": prompt,
        "expected_bug_type": bug_type,
        "expected_source_file": source_file_hint or "",
        "expected_difficulty": "hard",
        "expected_patch_risk": patch_risk,
        "expected_failure_category": failure_category,
        "should_apply_patch": str(should_apply_patch).lower(),
        "is_adversarial_stress": "true",
        "training_weight": "2.0",
    }


def export_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def export_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        raise ValueError("Cannot export CSV with no rows.")

    fieldnames = list(rows[0].keys())

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_report(
    fixtures_path: Path,
    jsonl_output: Path,
    csv_output: Path,
) -> dict[str, Any]:
    fixtures = load_fixtures(fixtures_path)
    rows = [make_training_row(case) for case in fixtures]

    export_jsonl(rows, jsonl_output)
    export_csv(rows, csv_output)

    categories = sorted({row["expected_failure_category"] for row in rows})
    patch_risks = sorted({row["expected_patch_risk"] for row in rows})

    all_blocked = all(row["should_apply_patch"] == "false" for row in rows)
    all_adversarial = all(row["is_adversarial_stress"] == "true" for row in rows)

    return {
        "passed": bool(rows) and all_blocked and all_adversarial,
        "phase": "phase7_stress_advisor_training_export",
        "fixtures_path": str(fixtures_path),
        "jsonl_output": str(jsonl_output),
        "csv_output": str(csv_output),
        "row_count": len(rows),
        "categories": categories,
        "patch_risks": patch_risks,
        "all_should_apply_patch_false": all_blocked,
        "all_adversarial_stress_true": all_adversarial,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Phase 7 stress advisor fixtures into training-compatible rows."
    )
    parser.add_argument(
        "--fixtures",
        default=DEFAULT_FIXTURES,
        help="Path to Phase 7 stress advisor fixture JSON.",
    )
    parser.add_argument(
        "--jsonl-output",
        default="benchmarks/.runs/phase7_stress_advisor_training_rows.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--csv-output",
        default="benchmarks/.runs/phase7_stress_advisor_training_rows.csv",
        help="Output CSV path.",
    )
    parser.add_argument(
        "--report-output",
        default="benchmarks/.runs/phase7_stress_advisor_training_export_report.json",
        help="Output report JSON path.",
    )

    args = parser.parse_args()

    report = build_report(
        fixtures_path=Path(args.fixtures).resolve(),
        jsonl_output=Path(args.jsonl_output).resolve(),
        csv_output=Path(args.csv_output).resolve(),
    )

    print(json.dumps(report, indent=2, sort_keys=True))

    report_output = Path(args.report_output).resolve()
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"\nPhase 7 training export report written to: {report_output}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
