from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.local_ai.provider import (
    OllamaProviderClient,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.main import create_app
from backend.app.project_retrieval.bindings import canonical_retrieval_authority_id
from backend.app.project_retrieval.contracts import CorpusIngestionRequest


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
    # LocalAIService's own hardware capability probe constructs its own
    # throwaway OllamaProviderClient independent of the generation gateway's
    # instance, so both must be patched at the class level for the model to
    # come back "locally available" as well as for generation to succeed.
    monkeypatch.setattr(OllamaProviderClient, "inspect", lambda self, *, timeout_seconds: fake.inspect(timeout_seconds=timeout_seconds))
    monkeypatch.setattr(OllamaProviderClient, "generate", lambda self, request, *, cancelled=None: fake.generate(request, cancelled=cancelled))
    local_ai.capability_report(refresh=True)
    # On a GPU-less test host, real admission falls back to CPU, which
    # requires interactive approval chat never has (by design -- no
    # automatic approval). This test is about chat wiring, not hardware
    # admission policy (already covered by the Stage 7D suite), so approval
    # is granted here the same way a GPU-admitted decision would need none.
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
        expected_version=version, idempotency_key="enable-chat-model-api-test",
    )
    return fake


def _create_project(app, root: Path) -> str:
    (root / "src").mkdir(parents=True)
    (root / "src" / "parser.py").write_text(
        "def parse(value: str) -> str:\n    \"\"\"Return the normalized parser value.\"\"\"\n    return value.strip()\n",
        encoding="utf-8",
    )
    project = app.state.canonical_project_service.create_project(
        conversation_id="api-conversation-1",
        workspace_id="api-workspace",
        repository_root=root,
        repository_root_fingerprint="api-root",
        actor_id="local-user",
        idempotency_key="create-api-project",
        folder_authority={
            "status": "completed", "action_id": "api-workspace",
            "conversation_id": "api-conversation-1", "workspace_id": "api-workspace",
            "repository_root_fingerprint": "api-root",
        },
        specification={
            "specification_id": "api-spec", "specification_hash": "1" * 64, "revision": 1,
            "included_paths": ["src"], "excluded_paths": [], "allowed_operations": ["read"],
        },
        manifest={
            "manifest_hash": "2" * 64, "complete": True, "revision": 1,
            "entries": [{"path": "src/parser.py", "sha256": "3" * 64}],
        },
        plan={"revision": 1, "acceptance_criteria": [], "work_units": []},
    )
    control = app.state.project_control
    retrieval = app.state.project_retrieval_service
    run = control.get_project(project.project_run_id)
    scope = control.get_scope_revision(run.current_scope_revision_id)
    plan = control.get_plan_revision(run.current_plan_revision_id)
    repository_state = retrieval.compute_repository_state(root, scope.included_paths, scope.excluded_paths)
    binding = {
        "project_id": run.project_run_id, "conversation_id": run.conversation_id,
        "actor_id": run.actor_id, "workspace_id": run.workspace_id,
        "repository_root": run.repository_root, "scope_revision_id": scope.scope_revision_id,
        "scope_hash": scope.content_hash, "plan_revision_id": plan.plan_revision_id,
        "plan_hash": plan.content_hash, "repository_manifest_hash": run.current_manifest_hash,
        "repository_state_hash": repository_state, "expected_project_state_version": run.state_version,
        "authority_id": canonical_retrieval_authority_id(run),
    }
    retrieval.ingest_project_corpus(CorpusIngestionRequest(**binding, idempotency_key="api-ingest"))
    return project.project_run_id


def test_chat_run_with_a_bound_project_answers_via_local_ai_with_citations(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ASTRA_LOCAL_AI_CHAT_MODEL", MODEL_TAG)
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        fake = _enable_chat_model(app, monkeypatch)
        project_run_id = _create_project(app, tmp_path / "repo")

        response = client.post(
            "/chat/run",
            json={
                "message": "Explain the parser code in this repo that normalizes values",
                "use_rag": True,
                "conversation_id": "api-conversation-1",
                "project_run_id": project_run_id,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["used_real_slm"] is True
    assert body["slm_provider"] == "ollama"
    assert body["assistant_response"] == "bounded answer from local ai"
    assert body["rag_used"] is True
    assert body["rag_context_count"] > 0
    assert body["rag_sources"][0]["path"] == "src/parser.py"
    assert fake.generate_calls == 1


def test_chat_run_without_a_configured_chat_role_falls_back_deterministically(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Hello there, what can you do?", "use_rag": False},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["used_real_slm"] is False
    assert body["slm_provider"] == "fallback"
    assert body["assistant_response"]


def test_chat_stream_with_a_bound_project_answers_via_local_ai(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ASTRA_LOCAL_AI_CHAT_MODEL", MODEL_TAG)
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        fake = _enable_chat_model(app, monkeypatch)
        # /chat/stream requires its conversation_id to already exist (unlike
        # /chat/run's permissive auto-vivify) -- prime it first so it matches
        # the project's conversation_id below.
        client.post(
            "/chat/run",
            json={"message": "priming this conversation", "use_rag": False, "conversation_id": "api-conversation-1"},
        )
        project_run_id = _create_project(app, tmp_path / "repo")

        response = client.post(
            "/chat/stream",
            json={
                "message": "Explain the parser code in this repo that normalizes values",
                "use_rag": True,
                "conversation_id": "api-conversation-1",
                "project_run_id": project_run_id,
            },
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    completed = next(event for event in events if event["event"] == "run_completed")
    run = completed["data"]["run"]
    assert run["used_real_slm"] is True
    assert run["rag_used"] is True
    # 2, not 1: the priming /chat/run call above also has a configured chat
    # role, so it generates too.
    assert fake.generate_calls == 2
