from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.pipeline import Pipeline


DEFAULT_INPUT = "benchmarks/.runs/repair_trace_dataset_latest.jsonl"
DEFAULT_MODEL_OUTPUT = "models/repair_trace_sequence/repair_trace_baseline.joblib"
DEFAULT_REPORT_OUTPUT = "benchmarks/.runs/repair_trace_baseline_report_latest.json"
BLOCKED_ACTIONS = {"apply_patch"}
SAFE_LABEL_FOR_BLOCKED_ACTIONS = "run_tests"
RANDOM_STATE = 42
START_ACTION = "<START>"
KNOWN_ACTIONS = [
    "search_files",
    "read_file",
    "analyze_ast",
    "run_tests",
    "validate_syntax",
    "propose_patch",
    "apply_patch",
    "rollback_patch",
    "final_response",
    "inspect_imports",
    "switch_target_file",
    "stop_repeated_reads",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train a classical repair trace baseline for ideal_next_action."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--model-output", default=DEFAULT_MODEL_OUTPUT)
    parser.add_argument("--report-output", default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--random-state", type=int, default=RANDOM_STATE)
    args = parser.parse_args()

    report = train_baseline(
        dataset_path=Path(args.input).resolve(),
        model_output=Path(args.model_output).resolve(),
        report_output=Path(args.report_output).resolve(),
        random_state=args.random_state,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nRepair trace baseline model written to: {Path(args.model_output).resolve()}")
    print(f"Repair trace baseline report written to: {Path(args.report_output).resolve()}")
    return 0


def train_baseline(
    *,
    dataset_path: Path,
    model_output: Path,
    report_output: Path,
    random_state: int = RANDOM_STATE,
) -> dict[str, Any]:
    rows = load_jsonl(dataset_path)
    train_rows = [row for row in rows if str(row.get("split")) == "train"]
    test_rows = [row for row in rows if str(row.get("split")) == "test"]
    if not train_rows:
        raise ValueError("Dataset must contain at least one train row.")
    if not test_rows:
        raise ValueError("Dataset must contain at least one test row.")

    y_train_raw = [str(row.get("ideal_next_action", "")) for row in train_rows]
    y_test_raw = [str(row.get("ideal_next_action", "")) for row in test_rows]
    y_train = [safe_label(label) for label in y_train_raw]
    y_test = [safe_label(label) for label in y_test_raw]
    x_train = [build_features(row) for row in train_rows]
    x_test = [build_features(row) for row in test_rows]

    model = build_model(y_train, random_state)
    model.fit(x_train, y_train)

    majority_model = DummyClassifier(strategy="most_frequent", random_state=random_state)
    majority_model.fit(x_train, y_train)

    start = time.perf_counter()
    predictions = safe_predictions(model.predict(x_test))
    elapsed = time.perf_counter() - start
    majority_predictions = safe_predictions(majority_model.predict(x_test))

    labels = sorted(set(y_train) | set(y_test) | set(predictions))
    report = build_report(
        dataset_path=dataset_path,
        train_rows=train_rows,
        test_rows=test_rows,
        y_train=y_train,
        y_test=y_test,
        y_train_raw=y_train_raw,
        y_test_raw=y_test_raw,
        predictions=predictions,
        majority_predictions=majority_predictions,
        labels=labels,
        prediction_elapsed_seconds=elapsed,
        model=model,
        random_state=random_state,
    )

    model_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "phase": "phase10_classical_sequence_baseline",
            "model": model,
            "feature_version": "repair_trace_sequence_features_v1",
            "blocked_output_actions": sorted(BLOCKED_ACTIONS),
            "safe_label_for_blocked_actions": SAFE_LABEL_FOR_BLOCKED_ACTIONS,
            "labels": labels,
            "random_state": random_state,
        },
        model_output,
    )

    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object.")
        rows.append(item)
    return rows


def build_model(y_train: list[str], random_state: int) -> Pipeline:
    classifier: LogisticRegression | DummyClassifier
    if len(set(y_train)) < 2:
        classifier = DummyClassifier(strategy="most_frequent", random_state=random_state)
    else:
        classifier = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=random_state,
        )
    return Pipeline(
        steps=[
            ("features", DictVectorizer(sparse=True)),
            ("classifier", classifier),
        ]
    )


def build_features(row: dict[str, Any]) -> dict[str, Any]:
    actions = normalized_actions(row.get("partial_actions"))
    action_counts = Counter(actions)
    features: dict[str, Any] = {
        "sequence_length": len(actions),
        "last_action": actions[-1] if actions else START_ACTION,
        "last_2_actions": action_window(actions, 2),
        "last_3_actions": action_window(actions, 3),
        "has_run_tests": int("run_tests" in actions),
        "has_analyze_ast": int("analyze_ast" in actions),
        "has_propose_patch": int("propose_patch" in actions),
        "read_file_count": action_counts.get("read_file", 0),
        "repeated_read_count": repeated_read_count(actions, row.get("files_read")),
        "unique_action_count": len(set(actions)),
        "advisor_runtime_mode": str(row.get("advisor_runtime_mode") or "off"),
        "test_status": str(row.get("test_status") or ""),
        "error_category": str(row.get("error_category") or ""),
        "has_advisor_next_action": int(bool(row.get("advisor_next_action"))),
        "advisor_next_action": str(row.get("advisor_next_action") or ""),
    }
    for action in KNOWN_ACTIONS:
        features[f"count_{action}"] = action_counts.get(action, 0)
    return features


def normalized_actions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(action) for action in value]


def action_window(actions: list[str], size: int) -> str:
    if not actions:
        return START_ACTION
    window = actions[-size:]
    padding = [START_ACTION] * max(0, size - len(window))
    return "|".join(padding + window)


def repeated_read_count(actions: list[str], files_read: Any) -> int:
    if not isinstance(files_read, list):
        return 0
    read_count = actions.count("read_file")
    unique_files_read = len({str(path) for path in files_read})
    return max(0, read_count - unique_files_read)


def safe_label(label: str) -> str:
    return SAFE_LABEL_FOR_BLOCKED_ACTIONS if label in BLOCKED_ACTIONS else label


def safe_predictions(predictions: Any) -> list[str]:
    return [safe_label(str(label)) for label in predictions]


def top_2_accuracy(model: Pipeline, x_test: list[dict[str, Any]], y_test: list[str]) -> float | None:
    if not hasattr(model, "predict_proba"):
        return None
    try:
        probabilities = model.predict_proba(x_test)
    except AttributeError:
        return None
    classes = [str(label) for label in model.classes_]
    if not classes:
        return None
    correct = 0
    for expected, row_probs in zip(y_test, probabilities):
        ranked_indexes = sorted(
            range(len(classes)),
            key=lambda index: row_probs[index],
            reverse=True,
        )
        top_labels = {safe_label(classes[index]) for index in ranked_indexes[:2]}
        correct += int(expected in top_labels)
    return correct / len(y_test) if y_test else 0.0


def build_report(
    *,
    dataset_path: Path,
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    y_train: list[str],
    y_test: list[str],
    y_train_raw: list[str],
    y_test_raw: list[str],
    predictions: list[str],
    majority_predictions: list[str],
    labels: list[str],
    prediction_elapsed_seconds: float,
    model: Pipeline,
    random_state: int,
) -> dict[str, Any]:
    class_report = classification_report(
        y_test,
        predictions,
        labels=labels,
        output_dict=True,
        zero_division=0,
    )
    per_class = {
        label: {
            "precision": round(float(class_report[label]["precision"]), 6),
            "recall": round(float(class_report[label]["recall"]), 6),
            "f1": round(float(class_report[label]["f1-score"]), 6),
            "support": int(class_report[label]["support"]),
        }
        for label in labels
    }
    matrix = confusion_matrix(y_test, predictions, labels=labels).tolist()
    prediction_count = len(predictions)
    apply_patch_prediction_count = sum(1 for item in predictions if item == "apply_patch")

    return {
        "phase": "phase10_classical_sequence_baseline",
        "input": str(dataset_path),
        "random_state": random_state,
        "feature_version": "repair_trace_sequence_features_v1",
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_label_distribution": dict(sorted(Counter(y_train).items())),
        "test_label_distribution": dict(sorted(Counter(y_test).items())),
        "input_apply_patch_label_count": sum(
            1 for label in y_train_raw + y_test_raw if label == "apply_patch"
        ),
        "blocked_apply_patch_output": True,
        "apply_patch_prediction_count": apply_patch_prediction_count,
        "majority_baseline_accuracy": round(
            float(accuracy_score(y_test, majority_predictions)),
            6,
        ),
        "model_accuracy": round(float(accuracy_score(y_test, predictions)), 6),
        "macro_f1": round(float(f1_score(y_test, predictions, average="macro", zero_division=0)), 6),
        "top_2_accuracy": round(top2, 6) if (top2 := top_2_accuracy(model, [build_features(row) for row in test_rows], y_test)) is not None else None,
        "confusion_matrix": {
            "labels": labels,
            "matrix": matrix,
        },
        "per_class": per_class,
        "prediction_latency_ms_mean": round(
            (prediction_elapsed_seconds / prediction_count) * 1000 if prediction_count else 0.0,
            6,
        ),
        "predictions": predictions,
    }


if __name__ == "__main__":
    raise SystemExit(main())
