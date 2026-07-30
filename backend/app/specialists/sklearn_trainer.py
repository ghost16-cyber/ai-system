from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from .dataset_loader import load_specialist_eval_dataset, validate_dataset_row
from .dataset_registry import get_dataset
from .feedback_logger import DEFAULT_SPECIALIST_FEEDBACK_PATH
from .model_audit_logger import append_model_audit_event
from .model_quality_gate import evaluate_quality_gate
from .model_store import build_model_metadata, save_specialist_model
from .registry import list_specialists
from .training_job_store import create_training_job, update_training_job


def train_specialist_models(
    *,
    dataset_path: str | Path | None = None,
    feedback_path: str | Path | None = None,
    model_dir: str | Path | None = None,
    thresholds: dict[str, float | int] | None = None,
    dataset_id: str | None = None,
    dataset_registry_path: str | Path | None = None,
    training_job_store_path: str | Path | None = None,
) -> dict[str, Any]:
    dataset_gate = _validate_training_dataset(dataset_id, dataset_registry_path)
    if dataset_gate["allowed"] is not True:
        return _blocked_training_summary(
            dataset_id=dataset_id,
            reason=dataset_gate["reason"],
            training_job_store_path=training_job_store_path,
        )
    if dataset_path is None and dataset_gate.get("dataset"):
        dataset_path = dataset_gate["dataset"]["path"]

    loaded = load_training_examples(dataset_path=dataset_path, feedback_path=feedback_path)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for example in loaded["examples"]:
        grouped[example["specialist"]].append(example)

    results = []
    for specialist in list_specialists():
        job = create_training_job(
            dataset_id=dataset_id,
            specialist_name=specialist,
            store_path=training_job_store_path,
        )
        update_training_job(
            job["training_job_id"],
            status="running",
            store_path=training_job_store_path,
        )
        try:
            result = train_one_specialist_model(
                specialist=specialist,
                examples=grouped.get(specialist, []),
                model_dir=model_dir,
                thresholds=thresholds,
                dataset_id=dataset_id,
                training_job_id=job["training_job_id"],
            )
            terminal_status = "completed" if result["saved"] else "rejected"
            job_record = update_training_job(
                job["training_job_id"],
                status=terminal_status,
                model_id=result.get("metadata", {}).get("model_id"),
                metrics={
                    "accuracy": result.get("accuracy", 0.0),
                    "label_counts": result.get("label_counts", {}),
                    "quality_gate": result.get("quality_gate", {}),
                    "example_count": result.get("example_count", 0),
                    "confusion_matrix": result.get("metadata", {})
                    .get("metrics", {})
                    .get("confusion_matrix"),
                    "precision": result.get("metadata", {}).get("metrics", {}).get("precision"),
                    "recall": result.get("metadata", {}).get("metrics", {}).get("recall"),
                    "f1_score": result.get("metadata", {}).get("metrics", {}).get("f1_score"),
                },
                error_message=None if result["saved"] else result.get("reason"),
                store_path=training_job_store_path,
            )
            result["training_job"] = job_record
            results.append(result)
        except Exception as error:
            job_record = update_training_job(
                job["training_job_id"],
                status="failed",
                error_message=str(error),
                store_path=training_job_store_path,
            )
            results.append(
                {
                    "specialist": specialist,
                    "example_count": len(grouped.get(specialist, [])),
                    "label_counts": {},
                    "saved": False,
                    "path": None,
                    "accuracy": 0.0,
                    "quality_gate": {},
                    "reason": "Training failed.",
                    "error": str(error),
                    "training_job": job_record,
                }
            )

    return {
        "trained": any(result["saved"] for result in results),
        "results": results,
        "sources": loaded["sources"],
        "validation_errors": loaded["validation_errors"],
    }


def load_training_examples(
    *,
    dataset_path: str | Path | None = None,
    feedback_path: str | Path | None = None,
) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    validation_errors: list[dict[str, Any]] = []

    dataset = load_specialist_eval_dataset(dataset_path)
    examples.extend(dataset["rows"])
    validation_errors.extend(
        {
            "source": "dataset",
            **error,
        }
        for error in dataset["errors"]
    )

    feedback_loaded = _load_feedback_examples(feedback_path or DEFAULT_SPECIALIST_FEEDBACK_PATH)
    examples.extend(feedback_loaded["rows"])
    validation_errors.extend(feedback_loaded["errors"])

    return {
        "examples": examples,
        "validation_errors": validation_errors,
        "sources": {
            "dataset": {
                "path": dataset["path"],
                "missing": dataset["missing"],
                "loaded_examples": len(dataset["rows"]),
            },
            "feedback": feedback_loaded["source"],
        },
    }


def train_one_specialist_model(
    *,
    specialist: str,
    examples: list[dict[str, Any]],
    model_dir: str | Path | None = None,
    thresholds: dict[str, float | int] | None = None,
    dataset_id: str | None = None,
    training_job_id: str | None = None,
) -> dict[str, Any]:
    label_counts = dict(sorted(Counter(example["expected_label"] for example in examples).items()))
    base_result = {
        "specialist": specialist,
        "example_count": len(examples),
        "label_counts": label_counts,
        "saved": False,
        "path": None,
    }

    if len(label_counts) < 2:
        quality_gate = evaluate_quality_gate(
            specialist=specialist,
            examples=examples,
            accuracy=0.0,
            thresholds=thresholds,
        )
        result = {
            **base_result,
            "accuracy": 0.0,
            "quality_gate": quality_gate,
            "reason": "Not enough labels to train a classifier.",
        }
        append_model_audit_event(
            action="model_rejected",
            specialist=specialist,
            details={"reason": result["reason"], "quality_gate": quality_gate},
        )
        return result

    split = _split_examples(examples)
    train_examples = split["train"]
    test_examples = split["test"]
    if len({example["expected_label"] for example in train_examples}) < 2:
        quality_gate = evaluate_quality_gate(
            specialist=specialist,
            examples=examples,
            accuracy=0.0,
            thresholds=thresholds,
        )
        result = {
            **base_result,
            "accuracy": 0.0,
            "quality_gate": quality_gate,
            "reason": "Training split does not contain enough labels.",
        }
        append_model_audit_event(
            action="model_rejected",
            specialist=specialist,
            details={"reason": result["reason"], "quality_gate": quality_gate},
        )
        return result

    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2))),
            (
                "classifier",
                LogisticRegression(max_iter=1000, random_state=23),
            ),
        ]
    )
    pipeline.fit(
        [example["text"] for example in train_examples],
        [example["expected_label"] for example in train_examples],
    )

    predicted = pipeline.predict([example["text"] for example in test_examples])
    expected = [example["expected_label"] for example in test_examples]
    accuracy = float(
        accuracy_score(
            expected,
            predicted,
        )
    )
    labels = sorted(set(expected) | set(predicted))
    precision, recall, f1_score, _support = precision_recall_fscore_support(
        expected,
        predicted,
        labels=labels,
        average="weighted",
        zero_division=0,
    )
    confusion = confusion_matrix(expected, predicted, labels=labels)
    evaluation_metrics = {
        "confusion_matrix": {
            label: {
                predicted_label: int(confusion[row_index][column_index])
                for column_index, predicted_label in enumerate(labels)
            }
            for row_index, label in enumerate(labels)
        },
        "precision": float(precision),
        "recall": float(recall),
        "f1_score": float(f1_score),
    }
    quality_gate = evaluate_quality_gate(
        specialist=specialist,
        examples=examples,
        accuracy=accuracy,
        thresholds=thresholds,
    )

    result = {
        **base_result,
        "accuracy": accuracy,
        "quality_gate": quality_gate,
        "train_examples": len(train_examples),
        "test_examples": len(test_examples),
        "reason": "Quality gate passed." if quality_gate["passed"] else "Quality gate failed.",
    }
    if not quality_gate["passed"]:
        metadata = build_model_metadata(
            specialist=specialist,
            accuracy=accuracy,
            label_counts=label_counts,
            train_examples=len(train_examples),
            test_examples=len(test_examples),
            quality_gate=quality_gate,
            lifecycle_status="rejected",
            dataset_id=dataset_id,
            training_job_id=training_job_id,
            extra_metrics=evaluation_metrics,
        )
        saved = save_specialist_model(
            specialist=specialist,
            pipeline=pipeline,
            metadata=metadata,
            model_dir=model_dir,
        )
        append_model_audit_event(
            action="model_rejected",
            model_id=saved["metadata"]["model_id"],
            specialist=specialist,
            details={
                "reason": result["reason"],
                "quality_gate": quality_gate,
                "path": saved["path"],
                "accuracy": accuracy,
                "training_job_id": training_job_id,
                "dataset_id": dataset_id,
            },
        )
        return {
            **result,
            "artifact_saved": True,
            "path": saved["path"],
            "metadata": saved["metadata"],
        }

    metadata = build_model_metadata(
        specialist=specialist,
        accuracy=accuracy,
        label_counts=label_counts,
        train_examples=len(train_examples),
        test_examples=len(test_examples),
        quality_gate=quality_gate,
        dataset_id=dataset_id,
        training_job_id=training_job_id,
        extra_metrics=evaluation_metrics,
    )
    saved = save_specialist_model(
        specialist=specialist,
        pipeline=pipeline,
        metadata=metadata,
        model_dir=model_dir,
    )
    result = {
        **result,
        "saved": True,
        "path": saved["path"],
        "metadata": saved["metadata"],
    }
    append_model_audit_event(
        action="model_trained",
        model_id=saved["metadata"]["model_id"],
        specialist=specialist,
        details={
            "path": saved["path"],
            "accuracy": accuracy,
            **evaluation_metrics,
            "lifecycle_status": saved["metadata"]["lifecycle_status"],
            "training_job_id": training_job_id,
            "dataset_id": dataset_id,
        },
    )
    return result


def _split_examples(examples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    labels = [example["expected_label"] for example in examples]
    label_counts = Counter(labels)
    stratify = labels if min(label_counts.values()) >= 2 else None
    test_size = max(1, int(round(len(examples) * 0.25)))
    if test_size < len(label_counts):
        test_size = len(label_counts)
    if test_size >= len(examples):
        test_size = max(1, len(examples) - 1)

    try:
        train, test = train_test_split(
            examples,
            test_size=test_size,
            random_state=23,
            shuffle=True,
            stratify=stratify,
        )
    except ValueError:
        train, test = train_test_split(
            examples,
            test_size=test_size,
            random_state=23,
            shuffle=True,
        )
    return {"train": list(train), "test": list(test)}


def _load_feedback_examples(path: str | Path) -> dict[str, Any]:
    feedback_path = Path(path)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    source = {
        "path": str(feedback_path),
        "missing": not feedback_path.exists(),
        "loaded_examples": 0,
    }
    if not feedback_path.exists():
        return {"rows": rows, "errors": errors, "source": source}

    with feedback_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as error:
                errors.append(
                    {
                        "source": "feedback",
                        "line": line_number,
                        "errors": [f"invalid json: {error.msg}"],
                    }
                )
                continue

            candidate = {
                "specialist": raw.get("specialist"),
                "text": raw.get("text"),
                "expected_label": raw.get("user_corrected_label") or raw.get("expected_label"),
                "metadata": {"source": raw.get("source"), "feedback": True},
            }
            row, row_errors = validate_dataset_row(candidate)
            if row is None:
                errors.append(
                    {
                        "source": "feedback",
                        "line": line_number,
                        "errors": row_errors,
                    }
                )
                continue
            rows.append(row)

    source["loaded_examples"] = len(rows)
    return {"rows": rows, "errors": errors, "source": source}


def _validate_training_dataset(
    dataset_id: str | None,
    dataset_registry_path: str | Path | None,
) -> dict[str, Any]:
    if not dataset_id:
        return {"allowed": True, "reason": "No dataset_id provided; using existing training behavior."}
    dataset = get_dataset(dataset_id, dataset_registry_path)
    if dataset is None:
        return {"allowed": False, "reason": f"Dataset not found: {dataset_id}"}
    status = dataset.get("status")
    if status != "approved":
        return {
            "allowed": False,
            "reason": f"Dataset {dataset_id} is {status}; specialist training requires approved datasets.",
            "dataset": dataset,
        }
    return {"allowed": True, "reason": "Dataset is approved.", "dataset": dataset}


def _blocked_training_summary(
    *,
    dataset_id: str | None,
    reason: str,
    training_job_store_path: str | Path | None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for specialist in list_specialists():
        job = create_training_job(
            dataset_id=dataset_id,
            specialist_name=specialist,
            store_path=training_job_store_path,
        )
        job = update_training_job(
            job["training_job_id"],
            status="rejected",
            error_message=reason,
            store_path=training_job_store_path,
        )
        results.append(
            {
                "specialist": specialist,
                "example_count": 0,
                "label_counts": {},
                "saved": False,
                "path": None,
                "accuracy": 0.0,
                "quality_gate": {},
                "reason": reason,
                "training_job": job,
            }
        )

    return {
        "trained": False,
        "blocked": True,
        "reason": reason,
        "results": results,
        "sources": {},
        "validation_errors": [],
    }
