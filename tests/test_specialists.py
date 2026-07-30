from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.orchestrator import Orchestrator, OrchestratorConfig
from backend.app.orchestrator.models import AdvisorOutput, ToolAction
from backend.app.specialists import (
    SpecialistRequest,
    append_specialist_feedback,
    append_model_audit_event,
    approve_dataset,
    archive_dataset,
    build_model_evaluation_report,
    build_specialist_dashboard,
    classify_error,
    evaluate_examples,
    evaluate_specialist_dataset,
    evaluate_quality_gate,
    get_dataset,
    get_specialist_trace,
    get_training_job,
    find_specialist_model,
    list_datasets,
    list_specialist_traces,
    list_specialists,
    list_specialist_models,
    list_training_jobs,
    load_jsonl_dataset,
    load_model_audit_events,
    load_specialist_model,
    predict_all,
    predict_intent,
    predict_with_sklearn_model,
    predict_with_specialist,
    promote_model,
    deactivate_model,
    register_dataset,
    route_specialist_task,
    reject_model,
    rollback_specialist_model,
    save_specialist_model,
    train_specialist_models,
)
from backend.app.specialists import benchmark_router, evaluate_router_regression, load_router_regression_examples
from backend.app.specialists.model_store import build_model_metadata


class HighConfidencePatchAdvisor:
    name = "specialist_patch_success"

    def analyze(self, state, policy):
        return AdvisorOutput(
            name=self.name,
            label="likely_to_pass",
            confidence=1.0,
            data={
                "specialist": "patch_success",
                "label": "likely_to_pass",
                "confidence": 1.0,
                "advisory_only": True,
            },
            reason="Synthetic high-confidence specialist signal for safety testing.",
        )


class ApplyPatchProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="apply_patch",
            reason="Try to apply a patch without edit permission.",
            args={
                "path": "alpha.py",
                "old": "return 1",
                "new": "return 2",
            },
        )


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "specialists.db")) as test_client:
        yield test_client


def test_intent_classifier_labels_core_task_types():
    examples = {
        "Fix the failing parser test": "code_repair",
        "Check CUDA and VRAM runtime policy": "runtime_check",
        "Write a status report for Astra": "report_generation",
        "Build a RAG index with FAISS embeddings": "rag_search",
        "Choose safe PyTorch training batch settings": "pytorch_training",
        "Hello Astra, how are you?": "general_chat",
    }

    for text, expected in examples.items():
        prediction = predict_intent(SpecialistRequest(text=text))
        assert prediction.label == expected
        assert prediction.specialist == "intent_classifier"
        assert prediction.advisory_only is True
        assert prediction.model_version == "rules_v1"
        assert 0.0 <= prediction.confidence <= 1.0


def test_error_classifier_labels_common_errors():
    examples = {
        "ModuleNotFoundError: No module named 'django'": "missing_import",
        "SyntaxError: invalid syntax": "syntax_error",
        "Error: Cannot find module 'vite'": "dependency_missing",
        "torch.cuda.OutOfMemoryError: CUDA out of memory": "cuda_oom",
        "npm ERR! failed to compile TypeScript error": "npm_build_error",
        "pytest AssertionError short test summary info": "pytest_failure",
        "Something unusual happened": "unknown_error",
    }

    for text, expected in examples.items():
        prediction = classify_error(SpecialistRequest(text=text))
        assert prediction.label == expected
        assert prediction.specialist == "error_classifier"
        assert prediction.advisory_only is True
        assert prediction.model_version == "rules_v1"


def test_specialist_registry_predicts_by_name_and_all():
    assert list_specialists() == ["intent_classifier", "error_classifier"]

    intent = predict_with_specialist(
        "intent",
        SpecialistRequest(text="Generate a report"),
    )
    assert intent.label == "report_generation"

    all_predictions = predict_all(SpecialistRequest(text="pytest failed"))
    assert [prediction.specialist for prediction in all_predictions] == [
        "intent_classifier",
        "error_classifier",
    ]

    with pytest.raises(KeyError):
        predict_with_specialist("unknown", SpecialistRequest(text="x"))


def test_specialist_api_endpoints_are_read_only_predictions(client):
    intent = client.post(
        "/specialists/intent",
        json={"text": "Fix this broken React test"},
    )
    assert intent.status_code == 200
    assert intent.json()["label"] == "code_repair"
    assert intent.json()["advisory_only"] is True

    error = client.post(
        "/specialists/error-classify",
        json={"text": "torch.cuda.OutOfMemoryError: CUDA out of memory"},
    )
    assert error.status_code == 200
    assert error.json()["label"] == "cuda_oom"

    both = client.post(
        "/specialists/predict",
        json={"text": "npm ERR! failed to compile"},
    )
    assert both.status_code == 200
    assert {item["specialist"] for item in both.json()} == {
        "intent_classifier",
        "error_classifier",
    }

    one = client.post(
        "/specialists/predict",
        json={"text": "Create a RAG index", "specialist": "intent_classifier"},
    )
    assert one.status_code == 200
    assert [item["specialist"] for item in one.json()] == ["intent_classifier"]

    missing = client.post(
        "/specialists/predict",
        json={"text": "x", "specialist": "not_real"},
    )
    assert missing.status_code == 404


def test_specialist_confidence_cannot_authorize_blocked_patch_action(tmp_path: Path):
    (tmp_path / "alpha.py").write_text("def value():\n    return 1\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=ApplyPatchProposer(),
        advisors=[HighConfidencePatchAdvisor()],
        config=OrchestratorConfig(max_steps=1),
    ).run(goal="Patch alpha", allow_edits=False)

    assert result.status == "blocked"
    specialist_output = next(
        item
        for item in result.trace["advisor_outputs"]
        if item["name"] == "specialist_patch_success"
    )
    assert specialist_output["confidence"] == 1.0
    assert specialist_output["data"]["advisory_only"] is True
    assert result.trace["tool_history"][0]["action"] == "apply_patch"
    assert result.trace["tool_history"][0]["allowed"] is False
    assert "File edits require allow_edits=true" in result.trace["tool_history"][0]["policy_reason"]


def test_dataset_loader_loads_valid_jsonl(tmp_path: Path):
    dataset = tmp_path / "specialist_eval_dataset.jsonl"
    dataset.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "specialist": "intent_classifier",
                        "text": "fix this python import error",
                        "expected_label": "code_repair",
                        "metadata": {"source": "test"},
                    }
                ),
                json.dumps(
                    {
                        "specialist": "error_classifier",
                        "text": "SyntaxError: invalid syntax",
                        "expected_label": "syntax_error",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_jsonl_dataset(dataset)

    assert loaded["missing"] is False
    assert loaded["errors"] == []
    assert len(loaded["rows"]) == 2
    assert loaded["rows"][0]["metadata"] == {"source": "test"}


def test_dataset_loader_handles_missing_file_safely(tmp_path: Path):
    loaded = load_jsonl_dataset(tmp_path / "missing.jsonl")

    assert loaded["missing"] is True
    assert loaded["rows"] == []
    assert loaded["errors"] == []


def test_dataset_loader_reports_malformed_rows_without_crashing(tmp_path: Path):
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text(
        '{"specialist":"intent_classifier","text":"Generate a report","expected_label":"report_generation"}\n'
        '{"specialist":"intent_classifier","text":"missing expected"}\n'
        '{not json}\n',
        encoding="utf-8",
    )

    loaded = load_jsonl_dataset(dataset)

    assert len(loaded["rows"]) == 1
    assert len(loaded["errors"]) == 2
    assert loaded["errors"][0]["line"] == 2
    assert "expected_label must be a non-empty string" in loaded["errors"][0]["errors"]
    assert loaded["errors"][1]["line"] == 3
    assert loaded["errors"][1]["errors"][0].startswith("invalid json:")


def test_evaluation_returns_accuracy_and_failures():
    summary = evaluate_examples(
        [
            {
                "specialist": "intent_classifier",
                "text": "Create a RAG index with FAISS",
                "expected_label": "rag_search",
            },
            {
                "specialist": "error_classifier",
                "text": "torch.cuda.OutOfMemoryError: CUDA out of memory",
                "expected_label": "syntax_error",
            },
        ]
    )

    assert summary["total_examples"] == 2
    assert summary["correct"] == 1
    assert summary["incorrect"] == 1
    assert summary["accuracy"] == 0.5
    assert summary["by_specialist"]["intent_classifier"]["accuracy"] == 1.0
    assert summary["by_specialist"]["error_classifier"]["accuracy"] == 0.0
    assert summary["label_counts"]["expected"]["rag_search"] == 1
    assert summary["confusion_matrix"]["syntax_error"]["cuda_oom"] == 1
    assert summary["failures"][0]["predicted_label"] == "cuda_oom"


def test_evaluation_loads_dataset_and_predictions_stay_advisory_only(tmp_path: Path):
    dataset = tmp_path / "eval.jsonl"
    dataset.write_text(
        '{"specialist":"intent_classifier","text":"Check CUDA runtime","expected_label":"runtime_check"}\n',
        encoding="utf-8",
    )

    summary = evaluate_specialist_dataset(str(dataset))

    assert summary["dataset"]["loaded_examples"] == 1
    assert summary["accuracy"] == 1.0
    assert summary["results"][0]["advisory_only"] is True


def test_feedback_logger_appends_jsonl(tmp_path: Path):
    feedback_path = tmp_path / "specialist_feedback.jsonl"

    first = append_specialist_feedback(
        {
            "specialist": "intent_classifier",
            "text": "fix this import",
            "expected_label": "code_repair",
            "predicted_label": "code_repair",
            "source": "unit-test",
        },
        feedback_path,
    )
    second = append_specialist_feedback(
        {
            "specialist": "error_classifier",
            "text": "SyntaxError",
            "expected_label": "syntax_error",
            "user_corrected_label": "syntax_error",
        },
        feedback_path,
    )

    rows = [
        json.loads(line)
        for line in feedback_path.read_text(encoding="utf-8").splitlines()
    ]
    assert first["saved"] is True
    assert second["saved"] is True
    assert len(rows) == 2
    assert rows[0]["source"] == "unit-test"
    assert rows[1]["user_corrected_label"] == "syntax_error"
    assert rows[0]["timestamp"]


def test_specialist_evaluate_and_feedback_api_endpoints(client, tmp_path: Path, monkeypatch):
    dataset = tmp_path / "api_eval.jsonl"
    dataset.write_text(
        '{"specialist":"error_classifier","text":"ModuleNotFoundError: No module named x","expected_label":"missing_import"}\n',
        encoding="utf-8",
    )

    evaluation = client.post(
        "/specialists/evaluate",
        json={"dataset_path": str(dataset)},
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["accuracy"] == 1.0
    assert evaluation.json()["results"][0]["advisory_only"] is True

    monkeypatch.chdir(tmp_path)
    feedback = client.post(
        "/specialists/feedback",
        json={
            "specialist": "intent_classifier",
            "text": "Generate a report",
            "expected_label": "report_generation",
            "predicted_label": "report_generation",
            "source": "api-test",
        },
    )
    assert feedback.status_code == 200
    assert feedback.json()["saved"] is True
    assert (tmp_path / "data/specialists/specialist_feedback.jsonl").exists()


def test_evaluation_cannot_authorize_blocked_patch_plan(tmp_path: Path):
    summary = evaluate_examples(
        [
            {
                "specialist": "intent_classifier",
                "text": "Fix the failing parser test",
                "expected_label": "code_repair",
            }
        ]
    )
    assert summary["accuracy"] == 1.0
    assert summary["results"][0]["advisory_only"] is True

    (tmp_path / "alpha.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=ApplyPatchProposer(),
        advisors=[HighConfidencePatchAdvisor()],
        config=OrchestratorConfig(max_steps=1),
    ).run(goal="Patch alpha", allow_edits=False)

    assert result.status == "blocked"
    assert result.trace["tool_history"][0]["allowed"] is False


def test_sklearn_trainer_handles_missing_dataset_safely(tmp_path: Path):
    summary = train_specialist_models(
        dataset_path=tmp_path / "missing.jsonl",
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "models",
    )

    assert summary["trained"] is False
    assert summary["sources"]["dataset"]["missing"] is True
    assert summary["sources"]["feedback"]["missing"] is True
    assert all(result["saved"] is False for result in summary["results"])


def test_sklearn_trainer_refuses_single_label_dataset(tmp_path: Path):
    dataset = tmp_path / "single_label.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "specialist": "intent_classifier",
                "text": f"fix broken parser test {index}",
                "expected_label": "code_repair",
            }
            for index in range(8)
        ],
    )

    summary = train_specialist_models(
        dataset_path=dataset,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "models",
    )
    intent = next(
        result for result in summary["results"] if result["specialist"] == "intent_classifier"
    )

    assert intent["saved"] is False
    assert intent["quality_gate"]["passed"] is False
    assert "single-label model promotion is not allowed" in intent["quality_gate"]["failures"]


def test_quality_gate_blocks_weak_model():
    examples = [
        {"expected_label": "code_repair"},
        {"expected_label": "code_repair"},
        {"expected_label": "runtime_check"},
        {"expected_label": "runtime_check"},
    ]

    gate = evaluate_quality_gate(
        specialist="intent_classifier",
        examples=examples,
        accuracy=0.25,
        thresholds={"min_examples": 4, "min_labels": 2, "min_accuracy": 0.9},
    )

    assert gate["passed"] is False
    assert any("minimum accuracy not met" in failure for failure in gate["failures"])


def test_model_store_saves_and_loads_metadata(tmp_path: Path):
    metadata = build_model_metadata(
        specialist="intent_classifier",
        accuracy=1.0,
        label_counts={"code_repair": 2, "runtime_check": 2},
        train_examples=3,
        test_examples=1,
        quality_gate={
            "specialist": "intent_classifier",
            "passed": True,
            "failures": [],
            "thresholds": {},
            "example_count": 4,
            "label_counts": {"code_repair": 2, "runtime_check": 2},
            "accuracy": 1.0,
        },
    )

    saved = save_specialist_model(
        specialist="intent_classifier",
        pipeline={"fake": "pipeline"},
        metadata=metadata,
        model_dir=tmp_path,
    )
    candidate_id = saved["metadata"]["model_id"]
    candidate_loaded = load_specialist_model("intent_classifier", tmp_path)
    promoted = promote_model(candidate_id, tmp_path)
    loaded = load_specialist_model("intent_classifier", tmp_path)
    listed = list_specialist_models(tmp_path)

    assert saved["saved"] is True
    assert saved["metadata"]["lifecycle_status"] == "candidate"
    assert candidate_loaded is None
    assert promoted["promoted"] is True
    assert loaded is not None
    assert loaded["metadata"]["lifecycle_status"] == "promoted"
    assert loaded["metadata"]["model_version"] == "sklearn_tfidf_logreg_v1"
    assert listed["models"][0]["valid"] is True


def test_sklearn_predictor_returns_specialist_prediction(tmp_path: Path):
    model_dir = _train_temp_intent_model(tmp_path)

    prediction = predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="please fix this failing import test"),
        str(model_dir),
    )

    assert prediction is not None
    assert prediction.specialist == "intent_classifier"
    assert prediction.advisory_only is True
    assert prediction.features["source"] == "sklearn"
    assert prediction.model_version == "sklearn_tfidf_logreg_v1"


def test_candidate_model_is_not_used_until_promoted(tmp_path: Path):
    dataset = tmp_path / "intent_train.jsonl"
    model_dir = tmp_path / "models"
    _write_jsonl(dataset, _intent_training_rows())
    summary = train_specialist_models(
        dataset_path=dataset,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=model_dir,
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
    )
    intent = next(
        result for result in summary["results"] if result["specialist"] == "intent_classifier"
    )

    assert intent["saved"] is True
    assert intent["metadata"]["lifecycle_status"] == "candidate"
    assert predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="fix broken parser test"),
        str(model_dir),
    ) is None


def test_model_deactivation_returns_prediction_to_rule_fallback(tmp_path: Path):
    model_dir = _train_temp_intent_model(tmp_path)
    active = load_specialist_model("intent_classifier", model_dir)
    assert active is not None

    deactivated = deactivate_model(active["metadata"]["model_id"], model_dir)
    prediction = predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="fix broken parser test"),
        str(model_dir),
    )

    assert deactivated["deactivated"] is True
    assert prediction is None


def test_rejected_candidate_cannot_be_promoted(tmp_path: Path):
    dataset = tmp_path / "intent_train.jsonl"
    model_dir = tmp_path / "models"
    _write_jsonl(dataset, _intent_training_rows())
    summary = train_specialist_models(
        dataset_path=dataset,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=model_dir,
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
    )
    intent = next(
        result for result in summary["results"] if result["specialist"] == "intent_classifier"
    )

    rejected = reject_model(intent["metadata"]["model_id"], model_dir)
    promoted = promote_model(intent["metadata"]["model_id"], model_dir)

    assert rejected["rejected"] is True
    assert promoted["promoted"] is False


def test_registry_falls_back_to_rule_based_classifier_if_model_missing(monkeypatch):
    monkeypatch.setattr(
        "backend.app.specialists.registry.predict_with_sklearn_model",
        lambda specialist, payload: None,
    )

    prediction = predict_with_specialist(
        "intent_classifier",
        SpecialistRequest(text="Build a RAG index with FAISS embeddings"),
    )

    assert prediction.label == "rag_search"
    assert prediction.model_version == "rules_v1"
    assert prediction.advisory_only is True


def test_sklearn_predictions_remain_advisory_only(tmp_path: Path):
    model_dir = _train_temp_intent_model(tmp_path)

    prediction = predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="check cuda runtime and vram"),
        str(model_dir),
    )

    assert prediction is not None
    assert prediction.advisory_only is True


def test_sklearn_confidence_cannot_authorize_blocked_plans(tmp_path: Path):
    model_dir = _train_temp_intent_model(tmp_path)
    prediction = predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="fix this code now"),
        str(model_dir),
    )
    assert prediction is not None
    assert prediction.advisory_only is True

    (tmp_path / "alpha.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=ApplyPatchProposer(),
        advisors=[HighConfidencePatchAdvisor()],
        config=OrchestratorConfig(max_steps=1),
    ).run(goal="Patch alpha", allow_edits=False)

    assert result.status == "blocked"
    assert result.trace["tool_history"][0]["allowed"] is False


def test_specialist_train_and_models_api_endpoints(client, tmp_path: Path):
    dataset = tmp_path / "train.jsonl"
    model_dir = tmp_path / "models"
    _write_jsonl(dataset, _intent_training_rows())

    train = client.post(
        "/specialists/train",
        json={
            "dataset_path": str(dataset),
            "feedback_path": str(tmp_path / "missing_feedback.jsonl"),
            "model_dir": str(model_dir),
            "thresholds": {"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
        },
    )

    assert train.status_code == 200
    assert train.json()["trained"] is True
    saved = _save_candidate_with_metrics(model_dir, accuracy=0.95)

    promote = client.post(
        f"/specialists/models/{saved['metadata']['model_id']}/promote",
        json={"model_dir": str(model_dir)},
    )
    assert promote.status_code == 200
    assert promote.json()["promoted"] is True

    models = client.get("/specialists/models", params={"model_dir": str(model_dir)})
    assert models.status_code == 200
    assert any(model["lifecycle_status"] == "promoted" for model in models.json()["models"])

    deactivate = client.post(
        f"/specialists/models/{saved['metadata']['model_id']}/deactivate",
        json={"model_dir": str(model_dir)},
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["deactivated"] is True


def test_model_audit_trail_records_events(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    append_model_audit_event(
        action="model_trained",
        model_id="model-1",
        specialist="intent_classifier",
        details={"accuracy": 1.0},
        path=audit_path,
    )
    append_model_audit_event(
        action="fallback_used",
        specialist="intent_classifier",
        details={"fallback": "rule_based"},
        path=audit_path,
    )

    model_audit = load_model_audit_events("model-1", audit_path)
    all_audit = load_model_audit_events(path=audit_path)

    assert model_audit["missing"] is False
    assert [event["action"] for event in model_audit["events"]] == ["model_trained"]
    assert [event["action"] for event in all_audit["events"]] == [
        "model_trained",
        "fallback_used",
    ]


def test_lifecycle_and_prediction_actions_are_audited(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model_dir = _train_temp_intent_model(tmp_path)
    active = load_specialist_model("intent_classifier", model_dir)
    assert active is not None

    prediction = predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="fix broken parser test"),
        str(model_dir),
    )
    assert prediction is not None

    audit = load_model_audit_events(active["metadata"]["model_id"])
    actions = {event["action"] for event in audit["events"]}
    assert "model_trained" in actions
    assert "model_promoted" in actions
    assert "model_used_for_prediction" in actions


def test_dataset_registry_registers_approves_and_archives(tmp_path: Path):
    dataset = tmp_path / "dataset.jsonl"
    registry_path = tmp_path / "dataset_registry.json"
    _write_jsonl(dataset, _intent_training_rows())

    registered = register_dataset(dataset, dataset_id="dataset-1", registry_path=registry_path)
    approved = approve_dataset("dataset-1", registry_path)
    archived = archive_dataset("dataset-1", registry_path)
    listed = list_datasets(registry_path)
    fetched = get_dataset("dataset-1", registry_path)

    assert registered["dataset"]["status"] == "validated"
    assert registered["dataset"]["sample_count"] == 8
    assert approved["approved"] is True
    assert archived["archived"] is True
    assert listed["datasets"][0]["dataset_id"] == "dataset-1"
    assert fetched["status"] == "archived"


def test_dataset_registry_api_endpoints(client, tmp_path: Path):
    dataset = tmp_path / "api_dataset.jsonl"
    registry_path = tmp_path / "api_dataset_registry.json"
    _write_jsonl(dataset, _intent_training_rows())

    registered = client.post(
        "/specialists/datasets/register",
        json={
            "dataset_path": str(dataset),
            "dataset_id": "api-dataset",
            "registry_path": str(registry_path),
        },
    )
    assert registered.status_code == 200
    assert registered.json()["dataset"]["status"] == "validated"

    approved = client.post(
        "/specialists/datasets/api-dataset/approve",
        json={"registry_path": str(registry_path)},
    )
    assert approved.status_code == 200
    assert approved.json()["approved"] is True

    fetched = client.get(
        "/specialists/datasets/api-dataset",
        params={"registry_path": str(registry_path)},
    )
    assert fetched.status_code == 200
    assert fetched.json()["dataset_id"] == "api-dataset"

    archived = client.post(
        "/specialists/datasets/api-dataset/archive",
        json={"registry_path": str(registry_path)},
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True


def test_training_job_records_are_created_for_training_runs(tmp_path: Path):
    dataset = tmp_path / "train_with_jobs.jsonl"
    dataset_registry = tmp_path / "dataset_registry.json"
    job_store = tmp_path / "training_jobs.json"
    _write_jsonl(dataset, _intent_training_rows())
    register_dataset(dataset, dataset_id="dataset-1", registry_path=dataset_registry)
    approve_dataset("dataset-1", dataset_registry)

    summary = train_specialist_models(
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "models",
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
        dataset_id="dataset-1",
        dataset_registry_path=dataset_registry,
        training_job_store_path=job_store,
    )
    jobs = list_training_jobs(job_store)
    completed = [
        job for job in jobs["jobs"] if job["specialist_name"] == "intent_classifier"
    ][0]

    assert summary["trained"] is True
    assert len(jobs["jobs"]) == 2
    assert completed["dataset_id"] == "dataset-1"
    assert completed["status"] == "completed"
    assert completed["model_id"]
    assert get_training_job(completed["training_job_id"], job_store) == completed


def test_training_job_api_endpoints(client, tmp_path: Path):
    dataset = tmp_path / "api_train_jobs.jsonl"
    dataset_registry = tmp_path / "api_dataset_registry.json"
    job_store = tmp_path / "api_training_jobs.json"
    model_dir = tmp_path / "api_models"
    _write_jsonl(dataset, _intent_training_rows())
    register_dataset(dataset, dataset_id="api-dataset", registry_path=dataset_registry)
    approve_dataset("api-dataset", dataset_registry)

    trained = client.post(
        "/specialists/train",
        json={
            "feedback_path": str(tmp_path / "missing_feedback.jsonl"),
            "model_dir": str(model_dir),
            "dataset_id": "api-dataset",
            "dataset_registry_path": str(dataset_registry),
            "training_job_store_path": str(job_store),
            "thresholds": {"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
        },
    )
    assert trained.status_code == 200

    jobs = client.get("/specialists/training-jobs", params={"store_path": str(job_store)})
    assert jobs.status_code == 200
    assert len(jobs.json()["jobs"]) == 2
    job_id = jobs.json()["jobs"][0]["training_job_id"]

    job = client.get(
        f"/specialists/training-jobs/{job_id}",
        params={"store_path": str(job_store)},
    )
    assert job.status_code == 200
    assert job.json()["training_job_id"] == job_id


def test_specialist_router_recommends_without_execution():
    routed = route_specialist_task(
        SpecialistRequest(text="CUDA out of memory on my laptop GPU")
    )

    assert routed["task_type"] == "runtime"
    assert routed["recommended_specialist"] == "runtime_specialist"
    assert routed["promoted_model_available"] is False
    assert routed["fallback_required"] is True
    assert routed["advisory_only"] is True
    assert routed["execution_allowed"] is False


def test_specialist_router_api_endpoint(client):
    routed = client.post(
        "/specialists/route",
        json={"text": "security token leaked in a patch"},
    )

    assert routed.status_code == 200
    assert routed.json()["recommended_specialist"] == "safety_specialist"
    assert routed.json()["advisory_only"] is True


def test_specialist_router_recommends_core_specialist_types():
    safety = route_specialist_task(SpecialistRequest(text="security token leak"))
    bug = route_specialist_task(SpecialistRequest(text="pytest failure traceback"))

    assert safety["recommended_specialist"] == "safety_specialist"
    assert bug["recommended_specialist"] == "bug_triage_specialist"
    assert safety["execution_allowed"] is False
    assert bug["execution_allowed"] is False


def test_specialist_router_shows_promoted_model_availability(tmp_path: Path):
    saved = _save_candidate_with_metrics(
        tmp_path / "models",
        accuracy=0.95,
        specialist="runtime_specialist",
    )
    promote_model(saved["metadata"]["model_id"], tmp_path / "models")

    routed = route_specialist_task(
        SpecialistRequest(text="CUDA runtime issue"),
        model_dir=str(tmp_path / "models"),
    )

    assert routed["recommended_specialist"] == "runtime_specialist"
    assert routed["promoted_model_available"] is True
    assert routed["model_id"] == saved["metadata"]["model_id"]
    assert routed["fallback_required"] is False


def test_specialist_router_ignores_rejected_and_deactivated_models(tmp_path: Path):
    rejected = _save_candidate_with_metrics(
        tmp_path / "rejected_models",
        accuracy=0.95,
        specialist="runtime_specialist",
        lifecycle_status="rejected",
    )
    rejected_route = route_specialist_task(
        SpecialistRequest(text="cuda vram runtime"),
        model_dir=str(tmp_path / "rejected_models"),
    )
    assert rejected["metadata"]["lifecycle_status"] == "rejected"
    assert rejected_route["promoted_model_available"] is False
    assert rejected_route["fallback_required"] is True

    deactivated = _save_candidate_with_metrics(
        tmp_path / "deactivated_models",
        accuracy=0.95,
        specialist="runtime_specialist",
    )
    promote_model(deactivated["metadata"]["model_id"], tmp_path / "deactivated_models")
    deactivate_model(deactivated["metadata"]["model_id"], tmp_path / "deactivated_models")
    deactivated_route = route_specialist_task(
        SpecialistRequest(text="cuda vram runtime"),
        model_dir=str(tmp_path / "deactivated_models"),
    )

    assert deactivated_route["promoted_model_available"] is False
    assert deactivated_route["fallback_required"] is True


def test_specialist_route_api_accepts_model_dir_context(client, tmp_path: Path):
    saved = _save_candidate_with_metrics(
        tmp_path / "api_models",
        accuracy=0.95,
        specialist="safety_specialist",
    )
    promote_model(saved["metadata"]["model_id"], tmp_path / "api_models")

    routed = client.post(
        "/specialists/route",
        json={
            "text": "security credential policy issue",
            "context": {"model_dir": str(tmp_path / "api_models")},
        },
    )

    assert routed.status_code == 200
    assert routed.json()["promoted_model_available"] is True
    assert routed.json()["model_id"] == saved["metadata"]["model_id"]


def test_specialist_router_creates_trace_without_raw_input(tmp_path: Path):
    trace_store = tmp_path / "traces.jsonl"
    raw_text = "CUDA runtime issue " + ("x" * 160)

    routed = route_specialist_task(
        SpecialistRequest(
            text=raw_text,
            context={"trace_store_path": str(trace_store)},
        )
    )
    traces = list_specialist_traces(path=trace_store)
    trace = traces["traces"][0]

    assert routed["recommended_specialist"] == "runtime_specialist"
    assert traces["count"] == 1
    assert trace["recommended_specialist"] == "runtime_specialist"
    assert trace["promoted_model_available"] is False
    assert trace["fallback_required"] is True
    assert trace["decision_source"] == "router"
    assert trace["input_hash"]
    assert trace["input_preview"] != raw_text
    assert len(trace["input_preview"]) <= 80


def test_specialist_trace_includes_model_id_when_promoted_model_exists(tmp_path: Path):
    model_dir = tmp_path / "models"
    trace_store = tmp_path / "traces.jsonl"
    saved = _save_candidate_with_metrics(
        model_dir,
        accuracy=0.95,
        specialist="runtime_specialist",
    )
    promote_model(saved["metadata"]["model_id"], model_dir)

    route_specialist_task(
        SpecialistRequest(
            text="cuda runtime issue",
            context={"trace_store_path": str(trace_store), "model_dir": str(model_dir)},
        )
    )
    trace = list_specialist_traces(path=trace_store)["traces"][0]

    assert trace["promoted_model_available"] is True
    assert trace["model_id"] == saved["metadata"]["model_id"]
    assert trace["fallback_required"] is False


def test_specialist_prediction_creates_trace_for_fallback(client, tmp_path: Path):
    trace_store = tmp_path / "prediction_traces.jsonl"
    response = client.post(
        "/specialists/predict",
        json={
            "text": "Create a RAG index",
            "specialist": "intent_classifier",
            "context": {"trace_store_path": str(trace_store)},
        },
    )
    traces = list_specialist_traces(path=trace_store)

    assert response.status_code == 200
    assert traces["count"] == 1
    assert traces["traces"][0]["request_type"] == "prediction"
    assert traces["traces"][0]["fallback_used"] is True
    assert traces["traces"][0]["decision_source"] == "rule_fallback"


def test_specialist_trace_api_empty_list_get_and_filters(client, tmp_path: Path):
    trace_store = tmp_path / "traces.jsonl"
    empty = client.get("/specialists/traces", params={"trace_store_path": str(trace_store)})
    assert empty.status_code == 200
    assert empty.json()["traces"] == []
    assert empty.json()["count"] == 0

    route_specialist_task(
        SpecialistRequest(
            text="security token leak",
            context={"trace_store_path": str(trace_store)},
        )
    )
    listed = client.get(
        "/specialists/traces",
        params={
            "trace_store_path": str(trace_store),
            "specialist_name": "safety_specialist",
            "fallback_used": True,
            "decision_source": "router",
        },
    )
    trace_id = listed.json()["traces"][0]["trace_id"]
    fetched = client.get(
        f"/specialists/traces/{trace_id}",
        params={"trace_store_path": str(trace_store)},
    )
    missing = client.get(
        "/specialists/traces/missing-trace",
        params={"trace_store_path": str(trace_store)},
    )

    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert fetched.status_code == 200
    assert fetched.json()["trace_id"] == trace_id
    assert get_specialist_trace(trace_id, trace_store)["trace_id"] == trace_id
    assert missing.status_code == 404


def test_specialist_dashboard_empty_state(client, tmp_path: Path):
    dashboard = client.get(
        "/specialists/dashboard",
        params={
            "model_dir": str(tmp_path / "missing_models"),
            "dataset_registry_path": str(tmp_path / "missing_datasets.json"),
            "training_job_store_path": str(tmp_path / "missing_jobs.json"),
            "audit_path": str(tmp_path / "missing_audit.jsonl"),
            "trace_store_path": str(tmp_path / "missing_traces.jsonl"),
        },
    )

    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["total_models"] == 0
    assert body["total_datasets"] == 0
    assert body["total_training_jobs"] == 0
    assert body["recent_audit_events"] == []
    assert body["recent_traces"] == []
    assert body["recent_trace_summary"]["total_recent_traces"] == 0
    assert body["fallback_status"]["rule_based_fallback_available"] is True
    assert body["read_only"] is True


def test_specialist_dashboard_counts_and_recent_events(client, tmp_path: Path):
    model_dir = tmp_path / "models"
    dataset_registry = tmp_path / "datasets.json"
    job_store = tmp_path / "jobs.json"
    audit_path = tmp_path / "audit.jsonl"
    trace_store = tmp_path / "traces.jsonl"

    candidate = _save_candidate_with_metrics(model_dir, accuracy=0.95, specialist="runtime_specialist")
    promoted = _save_candidate_with_metrics(model_dir, accuracy=0.95, specialist="safety_specialist")
    promote_model(promoted["metadata"]["model_id"], model_dir)
    rejected = _save_candidate_with_metrics(
        model_dir,
        accuracy=0.95,
        specialist="bug_triage_specialist",
        lifecycle_status="rejected",
    )
    deactivated = _save_candidate_with_metrics(
        model_dir,
        accuracy=0.95,
        specialist="code_quality_specialist",
    )
    promote_model(deactivated["metadata"]["model_id"], model_dir)
    deactivate_model(deactivated["metadata"]["model_id"], model_dir)

    dataset = tmp_path / "dataset.jsonl"
    _write_jsonl(dataset, _intent_training_rows())
    register_dataset(dataset, dataset_id="dash-dataset", registry_path=dataset_registry)
    approve_dataset("dash-dataset", dataset_registry)

    train_specialist_models(
        dataset_id="dash-dataset",
        dataset_registry_path=dataset_registry,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "trained_models",
        training_job_store_path=job_store,
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
    )
    append_model_audit_event(
        action="model_promoted",
        model_id=promoted["metadata"]["model_id"],
        specialist="safety_specialist",
        path=audit_path,
    )
    route_specialist_task(
        SpecialistRequest(
            text="security token leaked",
            context={"trace_store_path": str(trace_store), "model_dir": str(model_dir)},
        )
    )

    dashboard = client.get(
        "/specialists/dashboard",
        params={
            "model_dir": str(model_dir),
            "dataset_registry_path": str(dataset_registry),
            "training_job_store_path": str(job_store),
            "audit_path": str(audit_path),
            "trace_store_path": str(trace_store),
        },
    )

    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["total_models"] == 4
    assert body["models_by_status"]["candidate"] == 1
    assert body["models_by_status"]["promoted"] == 1
    assert body["models_by_status"]["rejected"] == 1
    assert body["models_by_status"]["deactivated"] == 1
    assert body["total_datasets"] == 1
    assert body["datasets_by_status"]["approved"] == 1
    assert body["total_training_jobs"] == 2
    assert body["training_jobs_by_status"]["completed"] >= 1
    assert body["recent_audit_events"][0]["action"] == "model_promoted"
    assert body["recent_trace_summary"]["total_recent_traces"] == 1
    assert body["recent_traces"][0]["recommended_specialist"] == "safety_specialist"
    assert body["fallback_status"]["promoted_model_count"] == 1
    assert body["read_only"] is True
    assert candidate["metadata"]["lifecycle_status"] == "candidate"
    assert rejected["metadata"]["lifecycle_status"] == "rejected"


def test_specialist_dashboard_builder_is_read_only(tmp_path: Path):
    saved = _save_candidate_with_metrics(tmp_path / "models", accuracy=0.95)
    before = list_specialist_models(tmp_path / "models")

    dashboard = build_specialist_dashboard(model_dir=str(tmp_path / "models"))
    after = list_specialist_models(tmp_path / "models")

    assert dashboard["total_models"] == 1
    assert before == after


def test_specialist_frontend_safe_empty_list_shapes(client, tmp_path: Path):
    models = client.get("/specialists/models", params={"model_dir": str(tmp_path / "models")})
    datasets = client.get(
        "/specialists/datasets",
        params={"registry_path": str(tmp_path / "datasets.json")},
    )
    jobs = client.get(
        "/specialists/training-jobs",
        params={"store_path": str(tmp_path / "jobs.json")},
    )
    traces = client.get(
        "/specialists/traces",
        params={"trace_store_path": str(tmp_path / "traces.jsonl")},
    )

    assert models.status_code == 200
    assert models.json()["models"] == []
    assert models.json()["count"] == 0
    assert datasets.json()["datasets"] == []
    assert datasets.json()["count"] == 0
    assert jobs.json()["jobs"] == []
    assert jobs.json()["count"] == 0
    assert traces.json()["traces"] == []
    assert traces.json()["count"] == 0


def test_router_regression_dataset_and_evaluation_are_read_only(tmp_path: Path):
    trace_store = tmp_path / "traces.jsonl"
    examples = load_router_regression_examples()
    before = list_specialist_traces(path=trace_store)
    evaluation = evaluate_router_regression(examples)
    after = list_specialist_traces(path=trace_store)

    assert {example["expected_task_type"] for example in examples} >= {
        "runtime",
        "safety",
        "bug_triage",
        "code_quality",
        "rag",
        "training",
        "general",
    }
    assert evaluation["total_examples"] == len(examples)
    assert evaluation["accuracy"] == 1.0
    assert evaluation["read_only"] is True
    assert before == after


def test_router_evaluation_and_benchmark_endpoints(client):
    evaluation = client.get("/specialists/router/evaluation")
    benchmark = client.get("/specialists/router/benchmark")

    assert evaluation.status_code == 200
    assert evaluation.json()["total_examples"] >= 7
    assert evaluation.json()["accuracy"] == 1.0
    assert benchmark.status_code == 200
    assert benchmark.json()["overall_accuracy"] == 1.0
    assert "runtime" in benchmark.json()["accuracy_by_task_type"]
    assert benchmark.json()["failures"] == []
    assert benchmark_router([])["overall_accuracy"] == 0.0



def test_candidate_with_good_metrics_can_be_promoted(tmp_path: Path):
    saved = _save_candidate_with_metrics(tmp_path, accuracy=0.95)
    promoted = promote_model(saved["metadata"]["model_id"], tmp_path)

    assert promoted["promoted"] is True
    assert promoted["metadata"]["lifecycle_status"] == "promoted"


def test_candidate_with_missing_metrics_cannot_be_promoted(tmp_path: Path):
    saved = _save_candidate_with_metrics(tmp_path, accuracy=0.95, include_metrics=False)
    promoted = promote_model(saved["metadata"]["model_id"], tmp_path)

    assert promoted["promoted"] is False
    assert "no stored metrics" in promoted["reason"]


def test_candidate_with_low_accuracy_cannot_be_promoted(tmp_path: Path):
    saved = _save_candidate_with_metrics(tmp_path, accuracy=0.25)
    promoted = promote_model(saved["metadata"]["model_id"], tmp_path)

    assert promoted["promoted"] is False
    assert "below minimum" in promoted["reason"]


def test_rejected_and_deactivated_models_cannot_be_promoted(tmp_path: Path):
    rejected = _save_candidate_with_metrics(
        tmp_path,
        accuracy=0.95,
        lifecycle_status="rejected",
    )
    rejected_promotion = promote_model(rejected["metadata"]["model_id"], tmp_path)
    assert rejected_promotion["promoted"] is False
    assert "Rejected models cannot be promoted" in rejected_promotion["reason"]

    active = _save_candidate_with_metrics(tmp_path / "deactivated", accuracy=0.95)
    promoted = promote_model(active["metadata"]["model_id"], tmp_path / "deactivated")
    assert promoted["promoted"] is True
    deactivated = deactivate_model(active["metadata"]["model_id"], tmp_path / "deactivated")
    deactivated_promotion = promote_model(active["metadata"]["model_id"], tmp_path / "deactivated")

    assert deactivated["deactivated"] is True
    assert deactivated_promotion["promoted"] is False
    assert "Only candidate models can be promoted" in deactivated_promotion["reason"]


def test_promotion_failure_response_is_clear_and_audited(client, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = _save_candidate_with_metrics(tmp_path / "models", accuracy=0.1)

    response = client.post(
        f"/specialists/models/{saved['metadata']['model_id']}/promote",
        json={"model_dir": str(tmp_path / "models")},
    )
    audit = load_model_audit_events(saved["metadata"]["model_id"])

    assert response.status_code == 400
    assert "below minimum" in response.json()["detail"]
    assert any(event["action"] == "model_promotion_blocked" for event in audit["events"])


def test_successful_promotion_is_logged(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = _save_candidate_with_metrics(tmp_path / "models", accuracy=0.95)
    promoted = promote_model(saved["metadata"]["model_id"], tmp_path / "models")
    audit = load_model_audit_events(saved["metadata"]["model_id"])

    assert promoted["promoted"] is True
    assert any(event["action"] == "model_promoted" for event in audit["events"])


def test_rule_based_fallback_still_works_without_promoted_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.app.specialists.registry.predict_with_sklearn_model",
        lambda specialist, payload: None,
    )
    prediction = predict_with_specialist(
        "intent_classifier",
        SpecialistRequest(text="Generate a status report"),
    )

    assert prediction.label == "report_generation"
    assert prediction.model_version == "rules_v1"


def test_training_with_approved_dataset_succeeds_and_stores_dataset_id(tmp_path: Path):
    dataset = tmp_path / "approved.jsonl"
    registry = tmp_path / "registry.json"
    _write_jsonl(dataset, _intent_training_rows())
    register_dataset(dataset, dataset_id="approved-dataset", registry_path=registry)
    approve_dataset("approved-dataset", registry)

    summary = train_specialist_models(
        dataset_id="approved-dataset",
        dataset_registry_path=registry,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "models",
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
    )
    intent = next(result for result in summary["results"] if result["specialist"] == "intent_classifier")

    assert summary["trained"] is True
    assert intent["metadata"]["dataset_id"] == "approved-dataset"
    assert intent["training_job"]["dataset_id"] == "approved-dataset"


@pytest.mark.parametrize("status", ["uploaded", "validated", "rejected", "archived"])
def test_training_with_unapproved_dataset_status_is_blocked(tmp_path: Path, status: str):
    dataset = tmp_path / f"{status}.jsonl"
    registry = tmp_path / f"{status}_registry.json"
    job_store = tmp_path / f"{status}_jobs.json"
    if status == "uploaded":
        register_dataset(tmp_path / "missing.jsonl", dataset_id=status, registry_path=registry)
    else:
        _write_jsonl(dataset, _intent_training_rows())
        register_dataset(dataset, dataset_id=status, registry_path=registry)
        if status == "rejected":
            archived_source = register_dataset(
                tmp_path / "missing_for_reject.jsonl",
                dataset_id=status,
                registry_path=registry,
            )
            assert archived_source["registered"] is True
            approve_dataset(status, registry)
        elif status == "archived":
            approve_dataset(status, registry)
            archive_dataset(status, registry)

    summary = train_specialist_models(
        dataset_id=status,
        dataset_registry_path=registry,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "models",
        training_job_store_path=job_store,
    )
    jobs = list_training_jobs(job_store)

    assert summary["blocked"] is True
    assert "requires approved datasets" in summary["reason"] or "failed validation" in summary["reason"]
    assert {job["status"] for job in jobs["jobs"]} == {"rejected"}


def test_training_with_missing_dataset_id_preserves_existing_behavior(tmp_path: Path):
    dataset = tmp_path / "no_id.jsonl"
    _write_jsonl(dataset, _intent_training_rows())

    summary = train_specialist_models(
        dataset_path=dataset,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=tmp_path / "models",
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
    )

    assert summary.get("blocked") is not True
    assert summary["trained"] is True


def test_blocked_training_api_returns_clear_error_and_records_jobs(client, tmp_path: Path):
    dataset = tmp_path / "validated.jsonl"
    registry = tmp_path / "validated_registry.json"
    job_store = tmp_path / "validated_jobs.json"
    _write_jsonl(dataset, _intent_training_rows())
    register_dataset(dataset, dataset_id="validated-only", registry_path=registry)

    response = client.post(
        "/specialists/train",
        json={
            "dataset_id": "validated-only",
            "dataset_registry_path": str(registry),
            "training_job_store_path": str(job_store),
        },
    )
    jobs = list_training_jobs(job_store)

    assert response.status_code == 400
    assert "requires approved datasets" in response.json()["detail"]
    assert {job["status"] for job in jobs["jobs"]} == {"rejected"}


def test_model_report_for_candidate_promoted_rejected_and_deactivated(tmp_path: Path):
    candidate = _save_candidate_with_metrics(
        tmp_path / "candidate",
        accuracy=0.95,
        dataset_id="dataset-x",
        training_job_id="job-x",
    )
    candidate_report = build_model_evaluation_report(
        candidate["metadata"]["model_id"],
        tmp_path / "candidate",
    )
    assert candidate_report["model_status"] == "candidate"
    assert candidate_report["dataset_id"] == "dataset-x"
    assert candidate_report["training_job_id"] == "job-x"
    assert candidate_report["metrics"]["accuracy"] == 0.95
    assert candidate_report["sample_count"] == 4

    promoted = promote_model(candidate["metadata"]["model_id"], tmp_path / "candidate")
    promoted_report = build_model_evaluation_report(
        candidate["metadata"]["model_id"],
        tmp_path / "candidate",
    )
    assert promoted["promoted"] is True
    assert promoted_report["model_status"] == "promoted"
    assert promoted_report["promoted_at"]

    deactivated = deactivate_model(candidate["metadata"]["model_id"], tmp_path / "candidate")
    deactivated_report = build_model_evaluation_report(
        candidate["metadata"]["model_id"],
        tmp_path / "candidate",
    )
    assert deactivated["deactivated"] is True
    assert deactivated_report["model_status"] == "deactivated"

    rejected = _save_candidate_with_metrics(
        tmp_path / "rejected",
        accuracy=0.95,
        lifecycle_status="rejected",
    )
    rejected_report = build_model_evaluation_report(
        rejected["metadata"]["model_id"],
        tmp_path / "rejected",
    )
    assert rejected_report["model_status"] == "rejected"


def test_model_report_endpoint_and_missing_model(client, tmp_path: Path):
    saved = _save_candidate_with_metrics(tmp_path / "models", accuracy=0.95)

    report = client.get(
        f"/specialists/models/{saved['metadata']['model_id']}/report",
        params={"model_dir": str(tmp_path / "models")},
    )
    missing = client.get(
        "/specialists/models/does-not-exist/report",
        params={"model_dir": str(tmp_path / "models")},
    )
    before = build_model_evaluation_report(saved["metadata"]["model_id"], tmp_path / "models")
    after = build_model_evaluation_report(saved["metadata"]["model_id"], tmp_path / "models")

    assert report.status_code == 200
    assert report.json()["model_id"] == saved["metadata"]["model_id"]
    assert missing.status_code == 404
    assert before["current_lifecycle_status"] == after["current_lifecycle_status"]


def test_specialist_missing_resource_endpoints_return_404(client, tmp_path: Path):
    model_body = {"model_dir": str(tmp_path / "models")}
    dataset_body = {"registry_path": str(tmp_path / "datasets.json")}

    assert client.post("/specialists/models/no-model/promote", json=model_body).status_code == 404
    assert client.post("/specialists/models/no-model/deactivate", json=model_body).status_code == 404
    assert client.post("/specialists/models/no-model/reject", json=model_body).status_code == 404
    assert client.post("/specialists/models/no-model/rollback", json=model_body).status_code == 404
    assert client.get(
        "/specialists/models/no-model/audit",
        params={"model_dir": str(tmp_path / "models")},
    ).status_code == 404
    assert client.get(
        "/specialists/training-jobs/no-job",
        params={"store_path": str(tmp_path / "jobs.json")},
    ).status_code == 404
    assert client.post(
        "/specialists/datasets/no-dataset/approve",
        json=dataset_body,
    ).status_code == 404
    assert client.post(
        "/specialists/datasets/no-dataset/archive",
        json=dataset_body,
    ).status_code == 404
    assert client.get(
        "/specialists/traces/no-trace",
        params={"trace_store_path": str(tmp_path / "traces.jsonl")},
    ).status_code == 404


def test_model_rollback_promotes_previous_good_model_and_prediction_uses_it(tmp_path: Path):
    model_dir = _train_temp_intent_model(tmp_path)
    previous = next(
        model
        for model in list_specialist_models(model_dir)["models"]
        if model["lifecycle_status"] == "promoted"
    )
    current = _save_candidate_with_metrics(
        model_dir,
        accuracy=0.95,
        specialist="intent_classifier",
    )
    promoted_current = promote_model(current["metadata"]["model_id"], model_dir)

    rollback = rollback_specialist_model(previous["model_id"], model_dir)
    active = load_specialist_model("intent_classifier", model_dir)
    prediction = predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="fix broken parser test", context={"trace_enabled": False}),
        str(model_dir),
    )
    audit = load_model_audit_events(previous["model_id"])

    assert promoted_current["promoted"] is True
    assert rollback["rolled_back"] is True
    assert active["metadata"]["model_id"] == previous["model_id"]
    assert prediction is not None
    assert prediction.advisory_only is True
    assert any(event["action"] == "model_rolled_back" for event in audit["events"])


def test_model_rollback_blocks_rejected_weak_missing_metrics_and_active(client, tmp_path: Path):
    model_dir = tmp_path / "rollback_models"
    active = _save_candidate_with_metrics(model_dir, accuracy=0.95)
    promote_model(active["metadata"]["model_id"], model_dir)
    rejected = _save_candidate_with_metrics(
        model_dir / "rejected",
        accuracy=0.95,
        lifecycle_status="rejected",
    )
    weak = _save_candidate_with_metrics(model_dir / "weak", accuracy=0.25)
    missing_metrics = _save_candidate_with_metrics(
        model_dir / "missing_metrics",
        accuracy=0.95,
        include_metrics=False,
    )

    assert rollback_specialist_model(active["metadata"]["model_id"], model_dir)["rolled_back"] is False
    assert rollback_specialist_model(rejected["metadata"]["model_id"], model_dir / "rejected")[
        "rolled_back"
    ] is False
    assert rollback_specialist_model(weak["metadata"]["model_id"], model_dir / "weak")[
        "rolled_back"
    ] is False
    assert rollback_specialist_model(
        missing_metrics["metadata"]["model_id"],
        model_dir / "missing_metrics",
    )["rolled_back"] is False

    failed = client.post(
        f"/specialists/models/{weak['metadata']['model_id']}/rollback",
        json={"model_dir": str(model_dir / "weak")},
    )
    assert failed.status_code == 400
    assert failed.json()["detail"]


def test_weak_trained_model_becomes_rejected_and_is_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dataset = tmp_path / "weak.jsonl"
    job_store = tmp_path / "weak_jobs.json"
    model_dir = tmp_path / "weak_models"
    _write_jsonl(dataset, _intent_training_rows())

    summary = train_specialist_models(
        dataset_path=dataset,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=model_dir,
        training_job_store_path=job_store,
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 1.1},
    )
    intent = next(result for result in summary["results"] if result["specialist"] == "intent_classifier")
    jobs = list_training_jobs(job_store)
    audit = load_model_audit_events(intent["metadata"]["model_id"])

    assert intent["saved"] is False
    assert intent["artifact_saved"] is True
    assert intent["metadata"]["lifecycle_status"] == "rejected"
    assert predict_with_sklearn_model(
        "intent_classifier",
        SpecialistRequest(text="fix broken parser test"),
        str(model_dir),
    ) is None
    assert promote_model(intent["metadata"]["model_id"], model_dir)["promoted"] is False
    assert any(event["action"] == "model_rejected" for event in audit["events"])
    rejected_job = next(job for job in jobs["jobs"] if job["specialist_name"] == "intent_classifier")
    assert rejected_job["status"] == "rejected"


def _train_temp_intent_model(tmp_path: Path) -> Path:
    dataset = tmp_path / "intent_train.jsonl"
    model_dir = tmp_path / "models"
    _write_jsonl(dataset, _intent_training_rows())
    summary = train_specialist_models(
        dataset_path=dataset,
        feedback_path=tmp_path / "missing_feedback.jsonl",
        model_dir=model_dir,
        thresholds={"min_examples": 8, "min_labels": 2, "min_accuracy": 0.0},
    )
    intent = next(
        result for result in summary["results"] if result["specialist"] == "intent_classifier"
    )
    assert intent["saved"] is True
    artifact = find_specialist_model(intent["metadata"]["model_id"], model_dir)
    assert artifact is not None
    metadata = {
        **artifact["artifact"]["metadata"],
        "accuracy": 0.95,
        "metrics": {
            **artifact["artifact"]["metadata"]["metrics"],
            "accuracy": 0.95,
            "precision": 0.95,
            "recall": 0.95,
            "f1_score": 0.95,
        },
    }
    metadata["quality_gate"] = {**metadata["quality_gate"], "passed": True, "accuracy": 0.95}
    metadata["metrics"]["quality_gate"] = metadata["quality_gate"]
    save_specialist_model(
        specialist="intent_classifier",
        pipeline=artifact["artifact"]["pipeline"],
        metadata=metadata,
        model_dir=model_dir,
    )
    promoted = promote_model(intent["metadata"]["model_id"], model_dir)
    assert promoted["promoted"] is True
    return model_dir


def _intent_training_rows() -> list[dict[str, str]]:
    return [
        {
            "specialist": "intent_classifier",
            "text": "fix broken parser test",
            "expected_label": "code_repair",
        },
        {
            "specialist": "intent_classifier",
            "text": "repair python import failure",
            "expected_label": "code_repair",
        },
        {
            "specialist": "intent_classifier",
            "text": "debug failing pytest error",
            "expected_label": "code_repair",
        },
        {
            "specialist": "intent_classifier",
            "text": "patch the syntax bug",
            "expected_label": "code_repair",
        },
        {
            "specialist": "intent_classifier",
            "text": "check cuda runtime and vram",
            "expected_label": "runtime_check",
        },
        {
            "specialist": "intent_classifier",
            "text": "inspect gpu hardware context",
            "expected_label": "runtime_check",
        },
        {
            "specialist": "intent_classifier",
            "text": "detect local runtime policy",
            "expected_label": "runtime_check",
        },
        {
            "specialist": "intent_classifier",
            "text": "verify pytorch cuda availability",
            "expected_label": "runtime_check",
        },
    ]


def _save_candidate_with_metrics(
    model_dir: Path,
    *,
    accuracy: float,
    specialist: str = "intent_classifier",
    include_metrics: bool = True,
    lifecycle_status: str = "candidate",
    dataset_id: str | None = None,
    training_job_id: str | None = None,
) -> dict:
    quality_gate = {
        "specialist": specialist,
        "passed": True,
        "failures": [],
        "thresholds": {},
        "example_count": 4,
        "label_counts": {"code_repair": 2, "runtime_check": 2},
        "accuracy": accuracy,
    }
    metadata = build_model_metadata(
        specialist=specialist,
        accuracy=accuracy,
        label_counts={"code_repair": 2, "runtime_check": 2},
        train_examples=3,
        test_examples=1,
        quality_gate=quality_gate,
        lifecycle_status=lifecycle_status,
        dataset_id=dataset_id,
        training_job_id=training_job_id,
        extra_metrics={
            "confusion_matrix": {
                "code_repair": {"code_repair": 1, "runtime_check": 0},
                "runtime_check": {"code_repair": 0, "runtime_check": 1},
            },
            "precision": accuracy,
            "recall": accuracy,
            "f1_score": accuracy,
        },
    )
    if not include_metrics:
        metadata.pop("metrics", None)
    return save_specialist_model(
        specialist=specialist,
        pipeline={"fake": "pipeline"},
        metadata=metadata,
        model_dir=model_dir,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
