from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd

from scripts.train_intent_router_from_combined_dataset import (
    evaluate_predictions,
    evaluate_quality_gate,
    load_combined_dataset,
    stratified_dataset_split,
    train_and_evaluate,
    train_candidate_model,
)


def _write_dataset(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _row(message: str, label: str) -> dict[str, str]:
    return {
        "user_message": message,
        "final_label": label,
        "label_status": "confirmed",
        "source": "test",
    }


def _balanced_rows(per_label: int = 4) -> list[dict[str, str]]:
    templates = {
        "backend": "FastAPI SQLite backend endpoint",
        "frontend": "React CSS frontend component",
        "debugging": "Python traceback exception error",
        "testing": "pytest unittest coverage mock",
        "rag": "LangChain FAISS embedding retrieval",
        "training": "sklearn classification training dataset",
        "runtime": "CUDA GPU memory performance server",
        "general": "What is the best way to design",
    }
    rows: list[dict[str, str]] = []
    for label, text in templates.items():
        for index in range(per_label):
            rows.append(_row(f"{text} example {index}", label))
    return rows


def test_combined_dataset_loading_validates_columns_and_rows(tmp_path: Path):
    dataset = tmp_path / "combined.csv"
    _write_dataset(dataset, _balanced_rows(2))

    loaded = load_combined_dataset(dataset)

    assert len(loaded) == 16
    assert set(loaded["final_label"]) == {
        "backend",
        "frontend",
        "debugging",
        "testing",
        "rag",
        "training",
        "runtime",
        "general",
    }


def test_stratified_split_preserves_labels(tmp_path: Path):
    dataset = tmp_path / "combined.csv"
    _write_dataset(dataset, _balanced_rows(4))
    loaded = load_combined_dataset(dataset)

    train, test = stratified_dataset_split(loaded, test_size=0.25)

    assert set(train["final_label"]) == set(test["final_label"])
    assert test["final_label"].value_counts().min() == 1


def test_model_training_output_and_metric_report_structure(tmp_path: Path):
    dataset = tmp_path / "combined.csv"
    _write_dataset(dataset, _balanced_rows(4))
    loaded = load_combined_dataset(dataset)
    train, test = stratified_dataset_split(loaded, test_size=0.25)

    model = train_candidate_model(train)
    predicted = list(model.predict(test["user_message"]))
    metrics = evaluate_predictions(test["final_label"].tolist(), predicted)

    assert 0 <= metrics["accuracy"] <= 1
    assert {"accuracy", "macro_precision", "macro_recall", "macro_f1", "per_label", "confusion_matrix"} <= set(metrics)
    assert set(metrics["per_label"]) == set(metrics["confusion_matrix"])


def test_quality_gate_pass_and_fail():
    pass_metrics = {
        "accuracy": 0.75,
        "macro_f1": 0.72,
        "per_label": {"backend": {"recall": 0.5, "support": 2}},
    }
    fail_metrics = {
        "accuracy": 0.69,
        "macro_f1": 0.72,
        "per_label": {"backend": {"recall": 0.49, "support": 2}},
    }

    assert evaluate_quality_gate(pass_metrics)["passed"] is True
    failed = evaluate_quality_gate(fail_metrics)
    assert failed["passed"] is False
    assert len(failed["failures"]) == 2


def test_training_saves_candidate_or_rejected_without_promotion(tmp_path: Path):
    dataset = tmp_path / "combined.csv"
    model_dir = tmp_path / "models"
    _write_dataset(dataset, _balanced_rows(5))

    result = train_and_evaluate(dataset_path=dataset, model_dir=model_dir, test_size=0.25)

    assert result["promoted"] is False
    assert result["lifecycle_status"] in {"candidate", "rejected"}
    assert result["metadata"]["lifecycle_status"] == result["lifecycle_status"]
    assert result["metadata"]["specialist"] == "intent_classifier"
    assert Path(result["model_path"]).exists()
    assert not (model_dir / "promoted" / "intent_classifier.joblib").exists()
    artifact = joblib.load(result["model_path"])
    assert artifact["metadata"]["model_id"] == result["metadata"]["model_id"]
