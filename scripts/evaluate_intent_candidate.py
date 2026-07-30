from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

from backend.app.specialists.model_quality_gate import evaluate_quality_gate


DEFAULT_CANDIDATE_PATH = Path(
    "data/specialists/models/candidates/intent_classifier_curated_20260709_140330/model.joblib"
)
REQUIRED_METRICS = (
    "accuracy",
    "macro_precision",
    "macro_recall",
    "macro_f1",
    "weighted_f1",
)
QUALITY_THRESHOLDS = {
    "minimum_accuracy": 0.70,
    "minimum_macro_precision": 0.70,
    "minimum_macro_recall": 0.70,
    "minimum_macro_f1": 0.70,
    "minimum_weighted_f1": 0.70,
    "minimum_label_recall": 0.50,
}


def evaluate_candidate(candidate_model_path: str | Path = DEFAULT_CANDIDATE_PATH) -> dict[str, Any]:
    model_path = Path(candidate_model_path)
    candidate_dir = model_path.parent
    metadata = _read_json(candidate_dir / "metadata.json")
    report = _read_json(candidate_dir / "evaluation_report.json")
    _load_model_for_validation(model_path)
    metrics = extract_metrics(metadata, report)
    gate = apply_intent_quality_gate(metadata, report, metrics)
    existing_gate = apply_existing_quality_gate(metadata, metrics)
    result = {
        "candidate_dir": str(candidate_dir),
        "model_path": str(model_path),
        "model_id": metadata.get("model_id") or report.get("model_id"),
        "evaluated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": metadata.get("status") or metadata.get("lifecycle_status") or report.get("status"),
        "metrics": metrics,
        "quality_gate": gate,
        "existing_model_quality_gate": existing_gate,
        "decision": "pass" if gate["passed"] else "fail",
        "promoted": False,
        "runtime_behavior_changed": False,
        "notes": [
            "Candidate evaluation only.",
            "No promotion was performed.",
            "Runtime routing behavior was not changed.",
        ],
    }
    output_path = candidate_dir / "quality_gate_result.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    result["quality_gate_result_path"] = str(output_path)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def extract_metrics(metadata: dict[str, Any], report: dict[str, Any]) -> dict[str, float]:
    report_metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    values: dict[str, float] = {}
    for key in REQUIRED_METRICS:
        raw = report_metrics.get(key, metadata.get(key))
        if not isinstance(raw, (int, float)):
            raise ValueError(f"Missing numeric metric: {key}")
        values[key] = float(raw)
    return values


def apply_intent_quality_gate(
    metadata: dict[str, Any],
    report: dict[str, Any],
    metrics: dict[str, float],
) -> dict[str, Any]:
    failures: list[str] = []
    metric_thresholds = {
        "accuracy": QUALITY_THRESHOLDS["minimum_accuracy"],
        "macro_precision": QUALITY_THRESHOLDS["minimum_macro_precision"],
        "macro_recall": QUALITY_THRESHOLDS["minimum_macro_recall"],
        "macro_f1": QUALITY_THRESHOLDS["minimum_macro_f1"],
        "weighted_f1": QUALITY_THRESHOLDS["minimum_weighted_f1"],
    }
    for metric, threshold in metric_thresholds.items():
        if metrics[metric] < threshold:
            failures.append(f"{metric} {metrics[metric]:.3f} < {threshold:.2f}")

    low_recall = _low_recall_labels(report)
    if low_recall:
        failures.append(
            "label recall below threshold: "
            + ", ".join(f"{label}={recall:.3f}" for label, recall in low_recall.items())
        )

    status = str(metadata.get("status") or metadata.get("lifecycle_status") or "")
    if status and status != "candidate":
        failures.append(f"candidate status expected; found {status}")

    return {
        "passed": not failures,
        "failures": failures,
        "thresholds": QUALITY_THRESHOLDS,
        "low_recall_labels": low_recall,
    }


def apply_existing_quality_gate(
    metadata: dict[str, Any],
    metrics: dict[str, float],
) -> dict[str, Any]:
    label_distribution = metadata.get("label_distribution")
    if not isinstance(label_distribution, dict):
        label_distribution = {}
    examples = [
        {"expected_label": label}
        for label, count in label_distribution.items()
        for _ in range(int(count or 0))
    ]
    return evaluate_quality_gate(
        specialist="intent_classifier",
        examples=examples,
        accuracy=metrics["accuracy"],
        thresholds={"min_accuracy": QUALITY_THRESHOLDS["minimum_accuracy"]},
    )


def _low_recall_labels(report: dict[str, Any]) -> dict[str, float]:
    classification = report.get("classification_report")
    if not isinstance(classification, dict):
        return {}
    low: dict[str, float] = {}
    for label, values in classification.items():
        if label in {"accuracy", "macro avg", "weighted avg"}:
            continue
        if not isinstance(values, dict):
            continue
        support = values.get("support", 0)
        recall = values.get("recall")
        if isinstance(recall, (int, float)) and float(support or 0) > 0:
            if float(recall) < QUALITY_THRESHOLDS["minimum_label_recall"]:
                low[str(label)] = float(recall)
    return dict(sorted(low.items()))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Missing candidate file: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"Candidate JSON file must contain an object: {path}")
    return payload


def _load_model_for_validation(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing candidate model: {path}")
    return joblib.load(path)


def print_result(result: dict[str, Any]) -> None:
    print(f"Candidate: {result['model_id']}")
    print(f"Model path: {result['model_path']}")
    print(f"Decision: {result['decision']}")
    print(f"Promoted: {result['promoted']}")
    print(f"Runtime behavior changed: {result['runtime_behavior_changed']}")
    print("Metrics:")
    for key, value in result["metrics"].items():
        print(f"- {key}: {value:.3f}")
    print("Quality gate:")
    print(f"- passed: {result['quality_gate']['passed']}")
    for failure in result["quality_gate"]["failures"]:
        print(f"- failure: {failure}")
    print(f"Saved: {result['quality_gate_result_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a candidate Astra intent classifier.")
    parser.add_argument("model_path", nargs="?", default=str(DEFAULT_CANDIDATE_PATH))
    args = parser.parse_args()
    print_result(evaluate_candidate(args.model_path))


if __name__ == "__main__":
    main()
