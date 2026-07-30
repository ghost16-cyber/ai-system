from __future__ import annotations

import json
from pathlib import Path

import joblib

from backend.app.orchestrator.models import OrchestratorConfig
from tools.analyze_repair_trace_dataset import analyze_rows
from tools.train_repair_trace_baseline import build_features, train_baseline


def test_repair_trace_dataset_analysis_reports_fixture_stats():
    rows = _fixture_rows()

    report = analyze_rows(rows)

    assert report["total_rows"] == 10
    assert report["train_rows"] == 6
    assert report["test_rows"] == 4
    assert report["ideal_next_action_distribution"]["run_tests"] == 3
    assert report["ideal_next_action_distribution"]["apply_patch"] == 1
    assert report["failure_mode_distribution"]["no_patch_proposed"] == 3
    assert report["intervention_needed_distribution"]["True"] == 4
    assert report["partial_trace_length"] == {"min": 0, "mean": 1.3, "max": 3}
    assert report["last_action_distribution"]["<START>"] == 3
    assert report["last_action_distribution"]["read_file"] == 2
    assert report["contains_apply_patch_ideal_next_action"] is True
    assert report["top_repeated_partial_action_patterns"][0] == {
        "pattern": ["search_files"],
        "count": 2,
    }
    assert report["class_imbalance_warning"] is False


def test_repair_trace_baseline_training_is_deterministic(tmp_path: Path):
    dataset_path = tmp_path / "repair_trace_dataset.jsonl"
    _write_jsonl(dataset_path, _fixture_rows())

    first = train_baseline(
        dataset_path=dataset_path,
        model_output=tmp_path / "model_a.joblib",
        report_output=tmp_path / "report_a.json",
        random_state=42,
    )
    second = train_baseline(
        dataset_path=dataset_path,
        model_output=tmp_path / "model_b.joblib",
        report_output=tmp_path / "report_b.json",
        random_state=42,
    )

    deterministic_keys = [
        "majority_baseline_accuracy",
        "model_accuracy",
        "macro_f1",
        "top_2_accuracy",
        "confusion_matrix",
        "per_class",
        "predictions",
    ]
    for key in deterministic_keys:
        assert first[key] == second[key]


def test_repair_trace_baseline_blocks_apply_patch_predictions(tmp_path: Path):
    dataset_path = tmp_path / "repair_trace_dataset.jsonl"
    _write_jsonl(dataset_path, _fixture_rows())

    report = train_baseline(
        dataset_path=dataset_path,
        model_output=tmp_path / "model.joblib",
        report_output=tmp_path / "report.json",
        random_state=42,
    )
    artifact = joblib.load(tmp_path / "model.joblib")
    predictions = artifact["model"].predict([build_features(row) for row in _fixture_rows()])

    assert report["input_apply_patch_label_count"] == 1
    assert report["blocked_apply_patch_output"] is True
    assert report["apply_patch_prediction_count"] == 0
    assert "apply_patch" not in artifact["labels"]
    assert "apply_patch" not in set(predictions)


def test_phase10_does_not_change_runtime_defaults():
    config = OrchestratorConfig()

    assert config.advisor_runtime_mode == "off"
    assert config.repair_trace_logging_enabled is True


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _fixture_rows() -> list[dict[str, object]]:
    specs = [
        ("train_case_a", "train", 1, [], "search_files", "search_files", "none", False),
        ("train_case_a", "train", 2, ["search_files"], "read_file", "read_file", "none", False),
        (
            "train_case_a",
            "train",
            3,
            ["search_files", "read_file"],
            "run_tests",
            "run_tests",
            "none",
            False,
        ),
        (
            "train_case_a",
            "train",
            4,
            ["search_files", "read_file", "run_tests"],
            "propose_patch",
            "propose_patch",
            "policy_block",
            True,
        ),
        ("train_case_b", "train", 1, [], "run_tests", "run_tests", "no_patch_proposed", True),
        (
            "train_case_b",
            "train",
            2,
            ["run_tests"],
            "analyze_ast",
            "analyze_ast",
            "no_patch_proposed",
            True,
        ),
        ("test_case_c", "test", 1, [], "search_files", "search_files", "none", False),
        ("test_case_c", "test", 2, ["search_files"], "read_file", "read_file", "none", False),
        (
            "test_case_c",
            "test",
            3,
            ["search_files", "read_file"],
            "run_tests",
            "run_tests",
            "none",
            False,
        ),
        (
            "test_case_c",
            "test",
            4,
            ["search_files", "read_file", "propose_patch"],
            "apply_patch",
            "apply_patch",
            "no_patch_proposed",
            True,
        ),
    ]
    return [
        _row(
            case_id=case_id,
            split=split,
            step_index=step_index,
            partial_actions=partial_actions,
            actual_next_action=actual_next_action,
            ideal_next_action=ideal_next_action,
            failure_mode=failure_mode,
            intervention_needed=intervention_needed,
        )
        for (
            case_id,
            split,
            step_index,
            partial_actions,
            actual_next_action,
            ideal_next_action,
            failure_mode,
            intervention_needed,
        ) in specs
    ]


def _row(
    *,
    case_id: str,
    split: str,
    step_index: int,
    partial_actions: list[str],
    actual_next_action: str,
    ideal_next_action: str,
    failure_mode: str,
    intervention_needed: bool,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "step_index": step_index,
        "timestamp": "2026-06-13T00:00:00+00:00",
        "advisor_runtime_mode": "off",
        "partial_actions": partial_actions,
        "files_read": ["app.py"] if "read_file" in partial_actions else [],
        "test_status": "failed" if "run_tests" in partial_actions else "",
        "error_category": "",
        "advisor_next_action": "",
        "actual_next_action": actual_next_action,
        "ideal_next_action": ideal_next_action,
        "final_status": "failed" if failure_mode != "none" else "completed",
        "failure_mode": failure_mode,
        "intervention_needed": intervention_needed,
        "split": split,
        "source_trace": f"trace_{case_id}.jsonl",
        "label_source": "fixture",
    }
