from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from backend.app.specialists.model_store import build_model_metadata, save_specialist_model
from backend.app.specialists.specialist_router import route_specialist_task


DEFAULT_DATASET_PATH = Path("data/specialists/intent_examples_combined.csv")
DEFAULT_MODEL_DIR = Path("models/specialists")
REQUIRED_COLUMNS = ("user_message", "final_label", "label_status", "source")
VALID_LABELS = (
    "backend",
    "frontend",
    "debugging",
    "testing",
    "rag",
    "training",
    "runtime",
    "general",
)
VALID_STATUSES = ("confirmed", "corrected")

QUALITY_THRESHOLDS = {
    "minimum_accuracy": 0.70,
    "minimum_macro_f1": 0.70,
    "minimum_label_recall": 0.50,
}

ROUTER_LABEL_MAP = {
    "general": "general",
    "bug_triage": "debugging",
    "code_quality": "debugging",
    "rag": "rag",
    "runtime": "runtime",
    "safety": "runtime",
    "training": "training",
}


def load_combined_dataset(path: str | Path = DEFAULT_DATASET_PATH) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    errors = validate_dataset(frame)
    if errors:
        raise ValueError("; ".join(errors))
    frame = frame[
        frame["final_label"].isin(VALID_LABELS)
        & frame["label_status"].isin(VALID_STATUSES)
    ].copy()
    if frame.empty:
        raise ValueError("No trainable rows found in combined dataset.")
    return frame


def validate_dataset(frame: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
        return errors
    invalid_labels = sorted(set(frame["final_label"]) - set(VALID_LABELS))
    if invalid_labels:
        errors.append(f"Invalid labels: {', '.join(invalid_labels)}")
    invalid_statuses = sorted(set(frame["label_status"]) - set(VALID_STATUSES))
    if invalid_statuses:
        errors.append(f"Invalid label_status values: {', '.join(invalid_statuses)}")
    return errors


def stratified_dataset_split(
    frame: pd.DataFrame,
    *,
    test_size: float = 0.25,
    random_state: int = 53,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    labels = frame["final_label"]
    label_counts = labels.value_counts()
    stratify = labels if not label_counts.empty and label_counts.min() >= 2 else None
    train, test = train_test_split(
        frame,
        test_size=test_size,
        random_state=random_state,
        shuffle=True,
        stratify=stratify,
    )
    return train.reset_index(drop=True), test.reset_index(drop=True)


def train_candidate_model(train_frame: pd.DataFrame) -> Pipeline:
    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
            ("classifier", LogisticRegression(max_iter=1000, random_state=53)),
        ]
    )
    pipeline.fit(train_frame["user_message"], train_frame["final_label"])
    return pipeline


def evaluate_predictions(
    expected: list[str],
    predicted: list[str],
    *,
    labels: tuple[str, ...] = VALID_LABELS,
) -> dict[str, Any]:
    accuracy = float(accuracy_score(expected, predicted))
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        expected,
        predicted,
        labels=list(labels),
        average="macro",
        zero_division=0,
    )
    per_precision, per_recall, per_f1, support = precision_recall_fscore_support(
        expected,
        predicted,
        labels=list(labels),
        average=None,
        zero_division=0,
    )
    confusion = confusion_matrix(expected, predicted, labels=list(labels))
    per_label = {
        label: {
            "precision": float(per_precision[index]),
            "recall": float(per_recall[index]),
            "f1": float(per_f1[index]),
            "support": int(support[index]),
        }
        for index, label in enumerate(labels)
    }
    return {
        "accuracy": accuracy,
        "macro_precision": float(macro_precision),
        "macro_recall": float(macro_recall),
        "macro_f1": float(macro_f1),
        "per_label": per_label,
        "confusion_matrix": {
            label: {
                predicted_label: int(confusion[row_index][column_index])
                for column_index, predicted_label in enumerate(labels)
            }
            for row_index, label in enumerate(labels)
        },
    }


def evaluate_quality_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if float(metrics["macro_f1"]) < QUALITY_THRESHOLDS["minimum_macro_f1"]:
        failures.append(
            f"macro F1 {metrics['macro_f1']:.3f} < {QUALITY_THRESHOLDS['minimum_macro_f1']:.2f}"
        )
    if float(metrics["accuracy"]) < QUALITY_THRESHOLDS["minimum_accuracy"]:
        failures.append(
            f"accuracy {metrics['accuracy']:.3f} < {QUALITY_THRESHOLDS['minimum_accuracy']:.2f}"
        )
    low_recall = {
        label: values["recall"]
        for label, values in metrics["per_label"].items()
        if values["support"] > 0 and values["recall"] < QUALITY_THRESHOLDS["minimum_label_recall"]
    }
    if low_recall:
        failures.append(
            "label recall below threshold: "
            + ", ".join(f"{label}={recall:.3f}" for label, recall in sorted(low_recall.items()))
        )
    return {
        "passed": not failures,
        "failures": failures,
        "thresholds": QUALITY_THRESHOLDS,
    }


def predict_rule_based_labels(messages: list[str]) -> list[str]:
    predicted: list[str] = []
    for message in messages:
        routed = route_specialist_task(
            {"text": message, "context": {"trace_enabled": False}},
        )
        task_type = str(routed.get("task_type") or "general")
        predicted.append(ROUTER_LABEL_MAP.get(task_type, "general"))
    return predicted


def compare_predictions(
    test_frame: pd.DataFrame,
    sklearn_predictions: list[str],
    rule_predictions: list[str],
    *,
    limit: int = 10,
) -> dict[str, Any]:
    expected = test_frame["final_label"].tolist()
    messages = test_frame["user_message"].tolist()
    sklearn_better: list[dict[str, str]] = []
    rule_better: list[dict[str, str]] = []
    for message, truth, sklearn_label, rule_label in zip(
        messages,
        expected,
        sklearn_predictions,
        rule_predictions,
    ):
        item = {
            "user_message": message,
            "expected": truth,
            "sklearn": sklearn_label,
            "rule_based": rule_label,
        }
        if sklearn_label == truth and rule_label != truth and len(sklearn_better) < limit:
            sklearn_better.append(item)
        if rule_label == truth and sklearn_label != truth and len(rule_better) < limit:
            rule_better.append(item)
    return {
        "sklearn_correct_rule_wrong": sklearn_better,
        "rule_correct_sklearn_wrong": rule_better,
    }


def train_and_evaluate(
    *,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    test_size: float = 0.25,
) -> dict[str, Any]:
    frame = load_combined_dataset(dataset_path)
    train_frame, test_frame = stratified_dataset_split(frame, test_size=test_size)
    pipeline = train_candidate_model(train_frame)
    sklearn_predictions = list(pipeline.predict(test_frame["user_message"]))
    expected = test_frame["final_label"].tolist()
    sklearn_metrics = evaluate_predictions(expected, sklearn_predictions)

    rule_predictions = predict_rule_based_labels(test_frame["user_message"].tolist())
    rule_metrics = evaluate_predictions(expected, rule_predictions)
    comparison = compare_predictions(test_frame, sklearn_predictions, rule_predictions)
    quality_gate = evaluate_quality_gate(sklearn_metrics)

    label_counts = dict(sorted(Counter(frame["final_label"]).items()))
    lifecycle_status = "candidate" if quality_gate["passed"] else "rejected"
    metadata = build_model_metadata(
        specialist="intent_classifier",
        accuracy=sklearn_metrics["accuracy"],
        label_counts=label_counts,
        train_examples=len(train_frame),
        test_examples=len(test_frame),
        quality_gate={
            "specialist": "intent_classifier",
            "passed": quality_gate["passed"],
            "failures": quality_gate["failures"],
            "thresholds": quality_gate["thresholds"],
            "example_count": len(frame),
            "label_counts": label_counts,
            "accuracy": sklearn_metrics["accuracy"],
        },
        lifecycle_status=lifecycle_status,
        dataset_id=Path(dataset_path).name,
        extra_metrics={
            "macro_precision": sklearn_metrics["macro_precision"],
            "macro_recall": sklearn_metrics["macro_recall"],
            "macro_f1": sklearn_metrics["macro_f1"],
            "per_label": sklearn_metrics["per_label"],
            "confusion_matrix": sklearn_metrics["confusion_matrix"],
            "rule_based_comparison": rule_metrics,
            "quality_gate_v2": quality_gate,
        },
    )
    saved = save_specialist_model(
        specialist="intent_classifier",
        pipeline=pipeline,
        metadata=metadata,
        model_dir=model_dir,
    )
    return {
        "dataset_path": str(dataset_path),
        "model_path": saved["path"],
        "metadata": saved["metadata"],
        "quality_gate": quality_gate,
        "lifecycle_status": lifecycle_status,
        "train_examples": len(train_frame),
        "test_examples": len(test_frame),
        "label_counts": label_counts,
        "sklearn_metrics": sklearn_metrics,
        "rule_based_metrics": rule_metrics,
        "comparison_examples": comparison,
        "promoted": False,
    }


def print_report(result: dict[str, Any]) -> None:
    print(f"Dataset: {result['dataset_path']}")
    print(f"Model path: {result['model_path']}")
    print(f"Lifecycle status: {result['lifecycle_status']}")
    print(f"Promoted automatically: {result['promoted']}")
    print(f"Train/test examples: {result['train_examples']} / {result['test_examples']}")
    print("Label counts:")
    for label, count in result["label_counts"].items():
        print(f"- {label}: {count}")
    print_side_by_side_metrics(result["sklearn_metrics"], result["rule_based_metrics"])
    print("Per-label sklearn metrics:")
    for label, values in result["sklearn_metrics"]["per_label"].items():
        print(
            f"- {label}: precision={values['precision']:.3f} "
            f"recall={values['recall']:.3f} f1={values['f1']:.3f} support={values['support']}"
        )
    print("Confusion matrix:")
    for label, row in result["sklearn_metrics"]["confusion_matrix"].items():
        print(f"- {label}: {row}")
    print("Quality gate:")
    print(f"- passed: {result['quality_gate']['passed']}")
    for failure in result["quality_gate"]["failures"]:
        print(f"- failure: {failure}")
    print_examples("Sklearn correct, rule-based wrong", result["comparison_examples"]["sklearn_correct_rule_wrong"])
    print_examples("Rule-based correct, sklearn wrong", result["comparison_examples"]["rule_correct_sklearn_wrong"])


def print_side_by_side_metrics(sklearn_metrics: dict[str, Any], rule_metrics: dict[str, Any]) -> None:
    print("Side-by-side metrics:")
    for name in ("accuracy", "macro_precision", "macro_recall", "macro_f1"):
        print(f"- {name}: sklearn={sklearn_metrics[name]:.3f} rule_based={rule_metrics[name]:.3f}")


def print_examples(title: str, examples: list[dict[str, str]]) -> None:
    print(f"{title}:")
    if not examples:
        print("- none")
        return
    for item in examples:
        print(
            f"- expected={item['expected']} sklearn={item['sklearn']} "
            f"rule={item['rule_based']} :: {item['user_message']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train and evaluate an Astra intent router candidate from the combined dataset."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    parser.add_argument("--test-size", type=float, default=0.25)
    args = parser.parse_args()
    result = train_and_evaluate(
        dataset_path=args.dataset,
        model_dir=args.model_dir,
        test_size=args.test_size,
    )
    print_report(result)


if __name__ == "__main__":
    main()
