from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.intelligence import (
    decision_traces_from_chat_runs,
    intelligence_components,
    model_use_policy,
    worker_roles,
)
from backend.app.schemas.api import ChatRunResponse


def _run() -> ChatRunResponse:
    return ChatRunResponse(
        run_id="run-1",
        conversation_id="conversation-1",
        user_message="How does the project RAG search work?",
        assistant_response="Use the indexed project sources.",
        selected_specialist="rag_specialist",
        intent="rag",
        confidence=0.82,
        rag_used=True,
        rag_skip_reason=None,
        rag_context_count=2,
        rag_sources=[],
        source_count=2,
        source_paths=["backend/app/rag/context_service.py"],
        grounding_status="grounded",
        runtime_decision="allow",
        safety_decision="allow",
        used_real_slm=False,
        slm_provider="fallback",
        slm_model=None,
        slm_fallback_reason="not configured",
        slm_latency_ms=None,
        memory_used=False,
        memory_summary=None,
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
        trace_summary=[
            {"phase": "specialist", "title": "Specialist routed"},
            {"phase": "rag", "title": "RAG search completed"},
            {"phase": "safety", "title": "Safety checked"},
        ],
    )


def test_intelligence_registry_lists_required_components():
    ids = {item["component_id"] for item in intelligence_components()}

    assert {
        "slm_gateway",
        "specialist_router",
        "promoted_specialist_models",
        "rag_search",
        "dataset_profiler",
        "assignment_copilot",
        "workers_jobs",
        "deterministic_safety_gates",
    }.issubset(ids)
    assert next(item for item in intelligence_components() if item["component_id"] == "deterministic_safety_gates")["role"] == "authoritative"


def test_model_use_policy_keeps_models_advisory_and_safety_deterministic():
    policy = model_use_policy()
    statements = " ".join(rule["statement"] for rule in policy["rules"]).lower()

    assert policy["automatic_promotion_allowed"] is False
    assert policy["slm_defaults_changed"] is False
    assert "path safety is always deterministic" in statements
    assert "specialist ml may classify task type but cannot approve actions" in statements


def test_worker_roles_expose_status_logs_paths_and_audit_events():
    roles = worker_roles()
    profiling = next(role for role in roles if role["role_id"] == "dataset_profiling")

    assert "status_fields" in profiling
    assert "logs" in profiling
    assert "safe_input_paths" in profiling
    assert "safe_output_paths" in profiling
    assert profiling["audit_event_type"]
    assert profiling["models_may_authorize"] is False


def test_decision_trace_normalizes_chat_run():
    traces = decision_traces_from_chat_runs([_run()])

    assert traces[0]["user_request"] == "How does the project RAG search work?"
    assert traces[0]["selected_specialist"] == "rag_specialist"
    assert traces[0]["rag"]["used"] is True
    assert traces[0]["slm"]["used"] is False
    assert "Safety checked" in traces[0]["deterministic_checks_applied"]
    assert traces[0]["final_safety_status"] == "allow"


def test_intelligence_dashboard_endpoint_is_read_only(tmp_path: Path):
    from backend.app.main import create_app

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.get("/intelligence/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["auditability"]["models_authorize_safety"] is False
    assert body["policy"]["automatic_promotion_allowed"] is False
    assert body["components"]
    assert body["worker_roles"]
