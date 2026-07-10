from __future__ import annotations

import json
from pathlib import Path

import joblib

from backend.app.specialists.specialist_router import route_specialist_task
from scripts.evaluate_intent_candidate import evaluate_candidate, extract_metrics


def _write_candidate(
    root: Path,
    *,
    accuracy: float = 0.92,
    macro_precision: float = 0.91,
    macro_recall: float = 0.9,
    macro_f1: float = 0.9,
    weighted_f1: float = 0.91,
    frontend_recall: float = 0.7,
) -> Path:
    candidate = root / "data" / "specialists" / "models" / "candidates" / "candidate"
    candidate.mkdir(parents=True)
    metadata = {
        "model_id": "candidate",
        "status": "candidate",
        "total_examples": 16,
        "label_distribution": {
            "backend": 2,
            "frontend": 2,
            "debugging": 2,
            "testing": 2,
            "rag": 2,
            "training": 2,
            "runtime": 2,
            "general": 2,
        },
        "accuracy": accuracy,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
    }
    report = {
        "model_id": "candidate",
        "status": "candidate",
        "metrics": {
            "accuracy": accuracy,
            "macro_precision": macro_precision,
            "macro_recall": macro_recall,
            "macro_f1": macro_f1,
            "weighted_f1": weighted_f1,
        },
        "classification_report": {
            "backend": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
            "frontend": {"precision": 1.0, "recall": frontend_recall, "f1-score": 0.8, "support": 2},
            "debugging": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
            "testing": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
            "rag": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
            "training": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
            "runtime": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
            "general": {"precision": 1.0, "recall": 1.0, "f1-score": 1.0, "support": 2},
        },
    }
    (candidate / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (candidate / "evaluation_report.json").write_text(json.dumps(report), encoding="utf-8")
    joblib.dump({"pipeline": object(), "metadata": metadata}, candidate / "model.joblib")
    return candidate / "model.joblib"


def test_valid_candidate_can_be_evaluated(tmp_path: Path):
    model_path = _write_candidate(tmp_path)

    result = evaluate_candidate(model_path)

    assert result["decision"] == "pass"
    assert result["quality_gate"]["passed"] is True
    assert result["promoted"] is False


def test_metrics_are_read_correctly(tmp_path: Path):
    model_path = _write_candidate(tmp_path, accuracy=0.88, macro_f1=0.87, weighted_f1=0.89)
    metadata = json.loads((model_path.parent / "metadata.json").read_text(encoding="utf-8"))
    report = json.loads((model_path.parent / "evaluation_report.json").read_text(encoding="utf-8"))

    metrics = extract_metrics(metadata, report)

    assert metrics["accuracy"] == 0.88
    assert metrics["macro_f1"] == 0.87
    assert metrics["weighted_f1"] == 0.89


def test_pass_fail_decision_is_saved(tmp_path: Path):
    model_path = _write_candidate(tmp_path, accuracy=0.6)

    result = evaluate_candidate(model_path)
    saved_path = Path(result["quality_gate_result_path"])
    saved = json.loads(saved_path.read_text(encoding="utf-8"))

    assert result["decision"] == "fail"
    assert saved["decision"] == "fail"
    assert saved["quality_gate"]["passed"] is False


def test_failed_candidates_are_not_promoted(tmp_path: Path):
    model_path = _write_candidate(tmp_path, macro_recall=0.6, frontend_recall=0.2)

    result = evaluate_candidate(model_path)

    assert result["decision"] == "fail"
    assert result["promoted"] is False
    assert not (tmp_path / "models" / "specialists" / "promoted" / "intent_classifier.joblib").exists()


def test_live_routing_still_does_not_change(tmp_path: Path):
    model_path = _write_candidate(tmp_path)
    before = route_specialist_task({"text": "Can you inspect RAG retrieval?", "context": {"trace_enabled": False}})

    result = evaluate_candidate(model_path)
    after = route_specialist_task({"text": "Can you inspect RAG retrieval?", "context": {"trace_enabled": False}})

    assert result["runtime_behavior_changed"] is False
    assert before["recommended_specialist"] == after["recommended_specialist"]
    assert before["task_type"] == after["task_type"]
