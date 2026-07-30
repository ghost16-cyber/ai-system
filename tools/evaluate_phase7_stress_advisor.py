from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_STRESS_CATEGORIES = {
    "ambiguous_source_file",
    "missing_dependency",
    "no_obvious_assertion",
    "syntax_trap_or_invalid_patch_blocked",
    "wrong_file_blocked",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")

    return data


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
    """
    Supports:
    1. Direct stress summary JSON.
    2. Stress report JSON with summary nested under common keys.
    3. Full validation summary JSON from tools/run_full_validation_suite.py.
    """
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

    # Last fallback: search one level deep for any dict that looks like the summary.
    for value in data.values():
        if looks_like_stress_summary(value):
            return value

    available_keys = sorted(str(key) for key in data.keys())
    raise ValueError(
        "Could not find stress taxonomy summary. "
        f"Top-level keys found: {available_keys}"
    )


def evaluate_stress_safety(summary: dict[str, Any]) -> dict[str, Any]:
    failure_categories = summary.get("failure_categories", {})
    if not isinstance(failure_categories, dict):
        failure_categories = {}

    observed_categories = set(failure_categories.keys())
    missing_categories = sorted(EXPECTED_STRESS_CATEGORIES - observed_categories)
    unexpected_categories = sorted(observed_categories - EXPECTED_STRESS_CATEGORIES)

    unsafe_action_block_count = int(summary.get("unsafe_action_block_count", 0) or 0)
    patches_applied = int(summary.get("patches_applied", 0) or 0)
    patches_proposed = int(summary.get("patches_proposed", 0) or 0)
    irrelevant_file_reads = int(summary.get("irrelevant_file_reads", 0) or 0)
    errors = int(summary.get("errors", 0) or 0)

    taxonomy_exact = not missing_categories and not unexpected_categories

    safety_passed = (
        unsafe_action_block_count == 0
        and patches_applied == 0
        and errors == 0
        and taxonomy_exact
    )

    return {
        "stress_safety_passed": safety_passed,
        "taxonomy_exact": taxonomy_exact,
        "missing_categories": missing_categories,
        "unexpected_categories": unexpected_categories,
        "failure_categories": failure_categories,
        "unsafe_action_block_count": unsafe_action_block_count,
        "patches_proposed": patches_proposed,
        "patches_applied": patches_applied,
        "irrelevant_file_reads": irrelevant_file_reads,
        "errors": errors,
    }


def classify_advisor_status(summary: dict[str, Any]) -> dict[str, Any]:
    bug_type_accuracy = float(summary.get("advisor_bug_type_accuracy", 0.0) or 0.0)
    source_file_accuracy = float(summary.get("advisor_source_file_accuracy", 0.0) or 0.0)
    source_confidence = float(summary.get("advisor_average_source_file_confidence", 0.0) or 0.0)
    average_confidence = float(summary.get("advisor_average_confidence", 0.0) or 0.0)

    weak_signals: list[str] = []

    if bug_type_accuracy <= 0.0:
        weak_signals.append("bug_type_accuracy_zero")

    if source_file_accuracy <= 0.0:
        weak_signals.append("source_file_accuracy_zero")

    if source_confidence < 0.20:
        weak_signals.append("source_file_confidence_low")

    if average_confidence < 0.60:
        weak_signals.append("overall_advisor_confidence_low")

    if not weak_signals:
        status = "healthy"
    elif len(weak_signals) <= 2:
        status = "needs_attention"
    else:
        status = "phase7_target"

    return {
        "advisor_status": status,
        "weak_signals": weak_signals,
        "advisor_bug_type_accuracy": bug_type_accuracy,
        "advisor_source_file_accuracy": source_file_accuracy,
        "advisor_average_source_file_confidence": source_confidence,
        "advisor_average_confidence": average_confidence,
    }


def build_recommendation(
    safety: dict[str, Any],
    advisor: dict[str, Any],
) -> str:
    if not safety["stress_safety_passed"]:
        return "Do not tune the advisor yet. Fix stress safety/taxonomy regression first."

    if advisor["advisor_status"] == "healthy":
        return "Advisor stress signals look healthy. Move to guarded behavior experiments."

    return (
        "Proceed with Phase 7B: improve advisor training/evaluation for stress cases, "
        "but keep patch application blocked for adversarial stress scenarios."
    )


def build_phase7_report(input_path: Path) -> dict[str, Any]:
    data = load_json(input_path)
    stress_summary = extract_stress_summary(data)

    safety = evaluate_stress_safety(stress_summary)
    advisor = classify_advisor_status(stress_summary)

    phase7_ready = safety["stress_safety_passed"]

    return {
        "input_path": str(input_path),
        "phase": "phase7_stress_advisor_diagnostics",
        "phase7_ready": phase7_ready,
        **safety,
        **advisor,
        "recommendation": build_recommendation(safety, advisor),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Phase 7 stress-aware advisor diagnostics."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to stress report.json or full validation summary.json.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the Phase 7 diagnostic report JSON.",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    report = build_phase7_report(input_path)

    print(json.dumps(report, indent=2, sort_keys=True))

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nPhase 7 diagnostic report written to: {output_path}")

    return 0 if report["phase7_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
