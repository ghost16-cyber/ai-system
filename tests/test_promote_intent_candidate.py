from __future__ import annotations

import json
from pathlib import Path

import joblib

from scripts.evaluate_intent_candidate import evaluate_candidate
from scripts.promote_intent_candidate import promote_candidate


class DummyPipeline:
    def predict(self, values):
        return ["general" for _ in values]


def _candidate(tmp_path: Path, *, passed: bool = True, include_gate: bool = True, include_model: bool = True) -> Path:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    metadata = {
        "model_id": "intent_candidate",
        "status": "candidate",
        "dataset_path": "data/specialists/intent_examples_curated.csv",
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
        "train_examples": 12,
        "test_examples": 4,
        "accuracy": 0.9 if passed else 0.5,
    }
    gate = {
        "decision": "pass" if passed else "fail",
        "metrics": {
            "accuracy": 0.9 if passed else 0.5,
            "macro_precision": 0.9,
            "macro_recall": 0.9,
            "macro_f1": 0.9,
            "weighted_f1": 0.9,
        },
        "quality_gate": {"passed": passed, "failures": [] if passed else ["too low"]},
    }
    (candidate / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    if include_gate:
        (candidate / "quality_gate_result.json").write_text(json.dumps(gate), encoding="utf-8")
    if include_model:
        joblib.dump(DummyPipeline(), candidate / "model.joblib")
    return candidate


def test_passing_candidate_can_be_promoted_manually(tmp_path: Path):
    candidate = _candidate(tmp_path)
    result = promote_candidate(candidate, model_dir=tmp_path / "models")

    assert result["promoted"] is True
    assert Path(result["promoted_model_path"]).exists()
    assert Path(result["promotion_result_path"]).exists()
    assert result["metadata"]["lifecycle_status"] == "promoted"


def test_failing_candidate_cannot_be_promoted(tmp_path: Path):
    candidate = _candidate(tmp_path, passed=False)
    result = promote_candidate(candidate, model_dir=tmp_path / "models")

    assert result["promoted"] is False
    assert "Quality gate did not pass" in result["reason"]
    assert not (tmp_path / "models" / "promoted" / "intent_classifier.joblib").exists()


def test_missing_quality_gate_result_blocks_promotion(tmp_path: Path):
    candidate = _candidate(tmp_path, include_gate=False)
    result = promote_candidate(candidate, model_dir=tmp_path / "models")

    assert result["promoted"] is False
    assert any("quality_gate_result.json" in item for item in result["missing_files"])


def test_missing_model_artifact_blocks_promotion(tmp_path: Path):
    candidate = _candidate(tmp_path, include_model=False)
    result = promote_candidate(candidate, model_dir=tmp_path / "models")

    assert result["promoted"] is False
    assert any("model.joblib" in item for item in result["missing_files"])


def test_promotion_writes_promotion_result_json(tmp_path: Path):
    candidate = _candidate(tmp_path)
    result = promote_candidate(candidate, model_dir=tmp_path / "models")
    saved = json.loads((candidate / "promotion_result.json").read_text(encoding="utf-8"))

    assert saved["promoted"] is True
    assert saved["promotion_result_path"] == result["promotion_result_path"]


def test_evaluation_does_not_automatically_promote(tmp_path: Path):
    candidate = _candidate(tmp_path)
    report = {
        "metrics": {
            "accuracy": 0.9,
            "macro_precision": 0.9,
            "macro_recall": 0.9,
            "macro_f1": 0.9,
            "weighted_f1": 0.9,
        },
        "classification_report": {
            "backend": {"recall": 1.0, "support": 2},
            "frontend": {"recall": 1.0, "support": 2},
        },
    }
    (candidate / "evaluation_report.json").write_text(json.dumps(report), encoding="utf-8")

    result = evaluate_candidate(candidate / "model.joblib")

    assert result["promoted"] is False
    assert not (tmp_path / "models" / "specialists" / "promoted" / "intent_classifier.joblib").exists()
