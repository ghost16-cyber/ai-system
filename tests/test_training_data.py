from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.training_data.label_policy import suggest_label
from backend.app.training_data.logger import redact_text


def _examples_path(workspace: Path) -> Path:
    return workspace / "data" / "training" / "intent_examples.jsonl"


def _read_examples(workspace: Path) -> list[dict]:
    path = _examples_path(workspace)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_chat_run_automatically_creates_training_example(tmp_path: Path):
    database_path = tmp_path / "app.db"
    with TestClient(create_app(database_path, workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Explain backend API routing", "use_rag": False},
        )
        status = client.get("/training/dataset/status")

    assert response.status_code == 200
    body = response.json()
    examples = _read_examples(tmp_path)
    assert len(examples) == 1
    assert examples[0]["source"] == "chat_run"
    assert examples[0]["chat_run_id"] == body["run_id"]
    assert examples[0]["user_message"] == "Explain backend API routing"
    assert examples[0]["suggested_label"] == "backend"
    assert examples[0]["label_status"] == "suggested"
    assert status.json()["total_examples"] == 1


def test_training_logger_does_not_duplicate_same_chat_run_id(tmp_path: Path):
    from backend.app import main as main_module

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Explain testing flow", "use_rag": False},
        )
        run = main_module.ChatRunResponse.model_validate(response.json())
        first_duplicate = main_module.log_chat_run_example(tmp_path, run)
        second_duplicate = main_module.log_chat_run_example(tmp_path, run)

    examples = _read_examples(tmp_path)
    assert len(examples) == 1
    assert first_duplicate["duplicate"] is True
    assert second_duplicate["duplicate"] is True


def test_redaction_removes_obvious_secrets():
    text = (
        "api_key=abc123secret\n"
        "PASSWORD: hunter2\n"
        "Authorization: Bearer abcdefghijklmnop\n"
        "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz\n"
        "keep this useful"
    )

    redacted = redact_text(text, max_chars=1000)

    assert "abc123secret" not in redacted
    assert "hunter2" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "[REDACTED]" in redacted
    assert "keep this useful" in redacted


def test_label_suggestion_covers_phase54_labels():
    assert suggest_label("Can you fix this React UI CSS bug?") == "frontend"
    assert suggest_label("Add a FastAPI endpoint backed by SQLite") == "backend"
    assert suggest_label("Run pytest and fix the build") == "testing"
    assert suggest_label("Where is project RAG indexing implemented?", rag_used=True, source_paths=["backend/app/rag/project_indexer.py"]) == "rag"
    assert suggest_label("hello there", routed_specialist="general_specialist") == "general"


def test_training_status_filters_and_label_updates(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/training/examples",
            json={
                "user_message": "Add a frontend dropdown",
                "assistant_response": "Use a select control.",
            },
        )
        example_id = created.json()["example"]["id"]
        confirmed = client.post(
            f"/training/examples/{example_id}/label",
            json={"label_status": "confirmed", "usefulness_rating": "good", "notes": "Looks useful."},
        )
        status = client.get("/training/dataset/status")
        filtered = client.get("/training/examples", params={"final_label": "frontend"})

    assert created.status_code == 200
    assert confirmed.status_code == 200
    updated = confirmed.json()["example"]
    assert updated["label_status"] == "confirmed"
    assert updated["final_label"] == "frontend"
    assert updated["usefulness_rating"] == "good"
    assert status.json()["total_examples"] == 1
    assert status.json()["labeled_count"] == 1
    assert status.json()["unlabeled_count"] == 0
    assert status.json()["label_distribution"] == {"frontend": 1}
    assert filtered.json()["items"][0]["id"] == example_id


def test_training_label_endpoint_corrects_and_rejects_examples(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/training/examples",
            json={"user_message": "Investigate failing API traceback"},
        )
        example_id = created.json()["example"]["id"]
        corrected = client.post(
            f"/training/examples/{example_id}/label",
            json={
                "label_status": "corrected",
                "corrected_label": "debugging",
                "usefulness_rating": "okay",
            },
        )
        rejected = client.post(
            f"/training/examples/{example_id}/label",
            json={"label_status": "rejected", "notes": "Too ambiguous."},
        )

    assert corrected.status_code == 200
    assert corrected.json()["example"]["final_label"] == "debugging"
    assert corrected.json()["example"]["label_status"] == "corrected"
    assert rejected.status_code == 200
    assert rejected.json()["example"]["final_label"] is None
    assert rejected.json()["example"]["label_status"] == "rejected"


def test_training_export_writes_only_confirmed_and_corrected_examples(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        frontend = client.post(
            "/training/examples",
            json={"user_message": "Fix React component layout"},
        ).json()["example"]
        backend = client.post(
            "/training/examples",
            json={"user_message": "Fix FastAPI endpoint"},
        ).json()["example"]
        client.post(
            "/training/examples",
            json={"user_message": "Unreviewed example"},
        )
        client.post(
            f"/training/examples/{frontend['id']}/label",
            json={"label_status": "confirmed"},
        )
        client.post(
            f"/training/examples/{backend['id']}/label",
            json={"label_status": "corrected", "corrected_label": "backend"},
        )
        jsonl_export = client.post("/training/export", json={"format": "jsonl"})
        csv_export = client.post("/training/export", json={"format": "csv"})

    assert jsonl_export.status_code == 200
    jsonl_body = jsonl_export.json()
    assert jsonl_body["row_count"] == 2
    assert jsonl_body["label_distribution"] == {"backend": 1, "frontend": 1}
    jsonl_rows = [
        json.loads(line)
        for line in Path(jsonl_body["path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert {row["final_label"] for row in jsonl_rows} == {"frontend", "backend"}

    assert csv_export.status_code == 200
    csv_body = csv_export.json()
    with Path(csv_body["path"]).open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 2
    assert {row["final_label"] for row in csv_rows} == {"frontend", "backend"}


def test_chat_run_still_stores_one_completed_run_with_training_logging(tmp_path: Path):
    database_path = tmp_path / "app.db"
    with TestClient(create_app(database_path, workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Explain runtime safety", "use_rag": False},
        )
        runs = client.get("/chat/runs")

    assert response.status_code == 200
    assert len(runs.json()["items"]) == 1
    assert len(_read_examples(tmp_path)) == 1
    with sqlite3.connect(database_path) as connection:
        stored_count = connection.execute("SELECT COUNT(*) FROM chat_runs").fetchone()[0]
    assert stored_count == 1
