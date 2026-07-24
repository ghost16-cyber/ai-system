from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.local_ai.provider import (
    OllamaProviderClient,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.main import create_app


MODEL_TAG = "qwen2.5-coder:1.5b"
MODEL_PROFILE_ID = "configured-local-model"
VALID_RESPONSE = (
    '{"schema_version":"astra.local-ai.advisory-response.v1",'
    '"response":"bounded answer from local ai"}'
)


class FakeProvider:
    def __init__(self) -> None:
        self.generate_calls = 0

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        del timeout_seconds
        return ProviderInspection("test", (MODEL_TAG,))

    def generate(self, request, *, cancelled=None) -> ProviderGenerationResponse:
        self.generate_calls += 1
        return ProviderGenerationResponse(
            model=MODEL_TAG, response=VALID_RESPONSE, metadata={"eval_count": 3},
        )


def _enable_chat_model(app, monkeypatch) -> FakeProvider:
    local_ai = app.state.local_ai_service
    fake = FakeProvider()
    monkeypatch.setattr(OllamaProviderClient, "inspect", lambda self, *, timeout_seconds: fake.inspect(timeout_seconds=timeout_seconds))
    monkeypatch.setattr(OllamaProviderClient, "generate", lambda self, request, *, cancelled=None: fake.generate(request, cancelled=cancelled))
    local_ai.capability_report(refresh=True)
    original_admission_preview = local_ai.admission_preview

    def _auto_approved_admission_preview(request, *, report=None):
        decision = original_admission_preview(request, report=report)
        if decision.requires_explicit_approval:
            decision = decision.model_copy(update={"requires_explicit_approval": False})
        return decision

    monkeypatch.setattr(local_ai, "admission_preview", _auto_approved_admission_preview)
    version = local_ai.configuration_state().configuration_version.model_profiles[MODEL_PROFILE_ID]
    local_ai.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=version, idempotency_key="enable-chat-model-replay-test",
    )
    return fake


def test_completed_stream_request_replays_without_regenerating(
    tmp_path: Path, monkeypatch,
) -> None:
    """A /chat/stream call reusing a completed request_id must return the
    stored run directly -- it must never re-enter chat_workflow or call
    execute_generation again."""

    monkeypatch.setenv("ASTRA_LOCAL_AI_CHAT_MODEL", MODEL_TAG)
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        fake = _enable_chat_model(app, monkeypatch)

        first = client.post(
            "/chat/stream",
            json={"message": "Explain the code in this repo", "use_rag": False},
        )
        assert first.status_code == 200
        events = [
            line for line in first.text.splitlines() if line.strip()
        ]
        import json as _json

        request_accepted = _json.loads(events[0])
        request_id = request_accepted["data"]["request"]["request_id"]
        conversation_id = request_accepted["data"]["request"]["conversation_id"]
        assert fake.generate_calls == 1

        second = client.post(
            "/chat/stream",
            json={
                "message": "Explain the code in this repo",
                "use_rag": False,
                "conversation_id": conversation_id,
                "request_id": request_id,
            },
        )

    assert second.status_code == 200
    second_events = [_json.loads(line) for line in second.text.splitlines() if line.strip()]
    assert [event["event"] for event in second_events] == ["request_accepted", "run_completed"]
    assert fake.generate_calls == 1


def test_retrying_a_request_id_with_a_changed_message_conflicts(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ASTRA_LOCAL_AI_CHAT_MODEL", MODEL_TAG)
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        _enable_chat_model(app, monkeypatch)

        first = client.post(
            "/chat/stream",
            json={"message": "Explain the code in this repo", "use_rag": False},
        )
        import json as _json

        request_accepted = _json.loads(first.text.splitlines()[0])
        request_id = request_accepted["data"]["request"]["request_id"]
        conversation_id = request_accepted["data"]["request"]["conversation_id"]

        conflicting = client.post(
            "/chat/stream",
            json={
                "message": "A completely different message",
                "use_rag": False,
                "conversation_id": conversation_id,
                "request_id": request_id,
            },
        )

    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "request_binding_mismatch"


def test_retrying_a_request_id_with_a_changed_project_run_id_conflicts(
    tmp_path: Path, monkeypatch,
) -> None:
    """Covers the Phase 9 extension of the conflict check: project binding
    is part of the request identity, not just message/conversation."""

    monkeypatch.setenv("ASTRA_LOCAL_AI_CHAT_MODEL", MODEL_TAG)
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        _enable_chat_model(app, monkeypatch)

        first = client.post(
            "/chat/stream",
            json={"message": "Explain the code in this repo", "use_rag": False},
        )
        import json as _json

        request_accepted = _json.loads(first.text.splitlines()[0])
        request_id = request_accepted["data"]["request"]["request_id"]
        conversation_id = request_accepted["data"]["request"]["conversation_id"]

        conflicting = client.post(
            "/chat/stream",
            json={
                "message": "Explain the code in this repo",
                "use_rag": False,
                "conversation_id": conversation_id,
                "request_id": request_id,
                "project_run_id": "some-other-project",
            },
        )

    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "request_binding_mismatch"
