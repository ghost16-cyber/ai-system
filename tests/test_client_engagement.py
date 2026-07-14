from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.client_engagement import (
    EngagementError,
    EngagementService,
    build_scope_revision,
    classify_scope_change,
    collect_authorized_evidence,
    estimate_effort,
    extract_requirements,
    parse_model_requirements,
)
from backend.app.client_engagement.contracts import (
    ENGAGEMENT_SCHEMA_VERSION,
    CreatorType,
    EngagementState,
    RequirementClassification,
    ScopeChangeClassification,
    ScopeRevision,
)
from backend.app.client_engagement.limits import EngagementLimits
from backend.app.client_engagement.scoping import canonical_scope_serialization, verify_scope_revision
from backend.app.database.repository import AnalysisRepository
from backend.app.main import create_app


WEBSITE = "Build a responsive restaurant website using the logo, menu, and images in the approved folder. Include a menu page and contact form."
REPAIR = "The application in this folder crashes when users upload a CSV. Diagnose and fix it without changing unrelated features."
DATA = "Analyze the supplied sales dataset and produce four charts and a short findings report."


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "client-project"
    root.mkdir()
    (root / "logo.png").write_bytes(b"png")
    (root / "menu.txt").write_text("Soup\nSalad\n", encoding="utf-8")
    (root / "app.py").write_text("def upload_csv(value):\n    return value\n", encoding="utf-8")
    (root / "sales.csv").write_text("month,sales\nJan,10\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=never-expose\n", encoding="utf-8")
    return root


def _service(tmp_path: Path) -> tuple[EngagementService, AnalysisRepository]:
    repository = AnalysisRepository(tmp_path / "engagement.db")
    repository.initialize()
    return EngagementService(repository), repository


def _website_scope(tmp_path: Path):
    service, repository = _service(tmp_path)
    project = _project(tmp_path)
    engagement = service.create(
        conversation_id="conversation", original_request=WEBSITE, user_id="user",
        folder_root=str(project), folder_access_id="access",
    )
    assert engagement["state"] == EngagementState.CLARIFICATION_REQUIRED.value
    answered = service.submit_answers(
        engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"],
        answers={}, answered_by="user", use_reasonable_assumptions=True,
    )
    assert answered["state"] == EngagementState.AWAITING_SCOPE_APPROVAL.value
    return service, repository, answered


def test_evidence_is_ordered_bounded_and_metadata_only(tmp_path: Path) -> None:
    project = _project(tmp_path)
    evidence = collect_authorized_evidence(
        engagement_id="engagement", conversation_id="conversation", original_request=WEBSITE,
        folder_root=project, folder_access_id="access", limits=EngagementLimits(max_evidence_items=4),
    )
    assert evidence[0].source_type.value == "original_chat_request"
    assert len(evidence) == 4
    serialized = json.dumps([item.model_dump(mode="json") for item in evidence])
    assert "never-expose" not in serialized
    assert all(item.structured_summary.get("content_included") is not True for item in evidence[1:])


def test_unauthorized_folder_and_symlink_escape_are_rejected_or_skipped(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (project / "escape.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="authorized"):
        collect_authorized_evidence(engagement_id="e", conversation_id="c", original_request="x", folder_root=project)
    evidence = collect_authorized_evidence(engagement_id="e", conversation_id="c", original_request="x", folder_root=project, folder_access_id="access")
    assert not any(item.source_identifier == "escape.txt" for item in evidence)


@pytest.mark.parametrize("client_text, expected", [
    (WEBSITE, {RequirementClassification.DELIVERABLE, RequirementClassification.NON_FUNCTIONAL, RequirementClassification.FUNCTIONAL}),
    (REPAIR, {RequirementClassification.DELIVERABLE, RequirementClassification.TECHNICAL_CONSTRAINT}),
    (DATA, {RequirementClassification.DELIVERABLE, RequirementClassification.FILE_REFERENCE}),
])
def test_deterministic_requirement_extraction_scenarios(tmp_path: Path, client_text: str, expected: set[RequirementClassification]) -> None:
    evidence = collect_authorized_evidence(engagement_id="e", conversation_id="c", original_request=client_text)
    requirements, assumptions, audit = extract_requirements(evidence)
    assert expected <= {item.classification for item in requirements}
    assert not assumptions
    assert audit["model_invoked"] is False


def test_model_output_rejects_unknown_fields_versions_and_fabricated_evidence() -> None:
    valid = {
        "schema_version": "astra.client-engagement.model-extraction.v1",
        "requirements": [{"text": "Build it", "classification": "deliverable", "evidence_ids": ["ev-1"], "explicit": True}],
        "assumptions": [],
    }
    parsed = parse_model_requirements(json.dumps(valid, separators=(",", ":")), valid_evidence_ids={"ev-1"})
    assert parsed.requirements[0].text == "Build it"
    with pytest.raises(ValueError):
        parse_model_requirements(json.dumps({**valid, "unknown": True}, separators=(",", ":")), valid_evidence_ids={"ev-1"})
    with pytest.raises(ValueError):
        parse_model_requirements(json.dumps({**valid, "schema_version": "v2"}, separators=(",", ":")), valid_evidence_ids={"ev-1"})
    with pytest.raises(ValueError, match="outside"):
        parse_model_requirements(json.dumps(valid, separators=(",", ":")), valid_evidence_ids={"different"})


def test_website_clarification_is_bounded_nonduplicative_and_assumptions_unblock(tmp_path: Path) -> None:
    service, _repository = _service(tmp_path)
    engagement = service.create(conversation_id="c", original_request=WEBSITE, user_id="u")
    questions = engagement["questions"]
    assert len(questions) <= 3
    assert {item["semantic_key"] for item in questions} == {"deployment_target", "form_delivery"}
    assert len({item["semantic_key"] for item in questions}) == len(questions)
    scoped = service.submit_answers(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], answers={}, answered_by="u", use_reasonable_assumptions=True)
    assert scoped["state"] == "awaiting_scope_approval"
    assert len(scoped["assumptions"]) == 2


def test_scope_has_acceptance_for_every_deliverable_and_transparent_estimate(tmp_path: Path) -> None:
    _service_value, _repository, engagement = _website_scope(tmp_path)
    revision = ScopeRevision.model_validate(engagement["current_scope"])
    assert all(item.acceptance_criteria for item in revision.scope.deliverables)
    assert all(item.review_mode.value in {"automated", "human_review_required"} for deliverable in revision.scope.deliverables for item in deliverable.acceptance_criteria)
    assert revision.scope.effort_estimate.guaranteed is False
    assert revision.scope.effort_estimate.expected.maximum >= revision.scope.effort_estimate.expected.minimum
    assert not any("$" in value for value in revision.scope.effort_estimate.assumptions)


def test_canonical_hash_detects_tampering_and_revision_is_immutable(tmp_path: Path) -> None:
    _service_value, _repository, engagement = _website_scope(tmp_path)
    revision = verify_scope_revision(engagement["current_scope"])
    assert revision.canonical_scope == canonical_scope_serialization(revision.scope)
    tampered = revision.model_dump(mode="json")
    tampered["scope"]["desired_outcome"] = "Invented outcome"
    with pytest.raises(ValueError, match="canonical"):
        verify_scope_revision(tampered)


def test_exact_approval_concurrency_duplicate_and_stale_version(tmp_path: Path) -> None:
    service, _repository, engagement = _website_scope(tmp_path)
    revision = engagement["current_scope"]
    with pytest.raises(EngagementError) as mismatch:
        service.approve_scope(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], revision_id=revision["revision_id"], scope_hash="0" * 64, approving_user="u")
    assert mismatch.value.code == "hash_mismatch"
    approved = service.approve_scope(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], revision_id=revision["revision_id"], scope_hash=revision["scope_hash"], approving_user="u")
    assert approved["state"] == "scope_approved"
    with pytest.raises(EngagementError) as stale:
        service.approve_scope(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], revision_id=revision["revision_id"], scope_hash=revision["scope_hash"], approving_user="u")
    assert stale.value.code == "conflict"


def test_idempotent_launch_creates_one_stage9_reference_and_keeps_approval_boundaries(tmp_path: Path) -> None:
    service, repository, engagement = _website_scope(tmp_path)
    revision = engagement["current_scope"]
    approved = service.approve_scope(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], revision_id=revision["revision_id"], scope_hash=revision["scope_hash"], approving_user="u")
    calls = []
    def launch(_engagement, _revision):
        calls.append(1)
        return {"delivery_job_id": "delivery-1", "specification": {"specification_hash": "a" * 64}, "plan_approval": None}
    launched, delivery = service.launch(engagement_id=approved["engagement_id"], expected_version=approved["state_version"], launch_stage9=launch, idempotency_key="launch-1")
    replayed, replay_delivery = service.launch(engagement_id=approved["engagement_id"], expected_version=approved["state_version"], launch_stage9=launch, idempotency_key="launch-1")
    assert len(calls) == 1
    assert launched["project_launch"]["delivery_job_id"] == "delivery-1"
    assert replayed["state"] == "project_launched"
    assert delivery["plan_approval"] is None
    assert replay_delivery["delivery_job_id"] == "delivery-1"
    count = sqlite3.connect(repository.database_path).execute("SELECT COUNT(*) FROM client_engagement_launches").fetchone()[0]
    assert count == 1


def test_launch_failure_is_retryable_and_does_not_mark_launched(tmp_path: Path) -> None:
    service, repository, engagement = _website_scope(tmp_path)
    revision = engagement["current_scope"]
    approved = service.approve_scope(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], revision_id=revision["revision_id"], scope_hash=revision["scope_hash"], approving_user="u")
    with pytest.raises(EngagementError) as failed:
        service.launch(engagement_id=approved["engagement_id"], expected_version=approved["state_version"], launch_stage9=lambda *_: (_ for _ in ()).throw(RuntimeError("secret stack")))
    assert failed.value.code == "launch_failed"
    current = repository.get_client_engagement(approved["engagement_id"])
    assert current["state"] == "scope_approved"
    assert current["project_launch"] is None


def test_material_scope_change_creates_new_revision_and_preserves_approved_scope(tmp_path: Path) -> None:
    service, _repository, engagement = _website_scope(tmp_path)
    revision = engagement["current_scope"]
    approved = service.approve_scope(engagement_id=engagement["engagement_id"], expected_version=engagement["state_version"], revision_id=revision["revision_id"], scope_hash=revision["scope_hash"], approving_user="u")
    changed = service.request_scope_change(engagement_id=approved["engagement_id"], expected_version=approved["state_version"], requested_change="Add customer accounts and online ordering.", requested_by="u")
    assert changed["state"] == "scope_change_review"
    assert len(changed["scope_revisions"]) == 2
    assert changed["scope_revisions"][0]["scope_hash"] == revision["scope_hash"]
    assert changed["scope_changes"][0]["classification"] == ScopeChangeClassification.ADDITION.value
    new_scope = ScopeRevision.model_validate(changed["current_scope"])
    assert any("customer accounts" in item.description.lower() for item in new_scope.scope.deliverables)
    assert new_scope.scope.effort_estimate.estimated_work_unit_count >= ScopeRevision.model_validate(revision).scope.effort_estimate.estimated_work_unit_count


@pytest.mark.parametrize("text, classification", [
    ("Add authentication", ScopeChangeClassification.ADDITION),
    ("Remove the menu page", ScopeChangeClassification.REMOVAL),
    ("Change the target framework", ScopeChangeClassification.CONSTRAINT),
    ("Change the acceptance criterion", ScopeChangeClassification.ACCEPTANCE),
    ("Cancel this engagement", ScopeChangeClassification.CANCELLATION),
])
def test_scope_change_classification(text: str, classification: ScopeChangeClassification) -> None:
    assert classify_scope_change(text) == classification


def _connect(client: TestClient, project: Path) -> str:
    requested = client.post("/chat/run", json={"message": f"Use {project}", "use_rag": True}).json()
    approved = client.post(f"/chat/folders/{requested['action']['action_id']}/approve", json={"chat_run_id": requested["run_id"]})
    assert approved.status_code == 200
    return requested["conversation_id"]


def test_api_intake_to_launch_reload_and_cross_conversation_protection(tmp_path: Path) -> None:
    project = _project(tmp_path)
    database = tmp_path / "api.db"
    with TestClient(create_app(database, tmp_path)) as client:
        conversation = _connect(client, project)
        created = client.post("/chat/client-engagements", json={"conversation_id": conversation, "client_request": REPAIR, "user_id": "user", "idempotency_key": "create-1"})
        assert created.status_code == 200, created.text
        public = created.json()["action"]["technical_details"]["client_engagement"]
        engagement_id = public["engagement_id"]
        assert public["state"] == "awaiting_scope_approval"
        revision = public["current_scope_revision"]
        wrong = client.get(f"/chat/client-engagements/{engagement_id}", params={"conversation_id": "other"})
        assert wrong.status_code == 409
        approved = client.post(f"/chat/client-engagements/{engagement_id}/scope/approve", json={
            "conversation_id": conversation, "expected_state_version": public["state_version"],
            "revision_id": revision["revision_id"], "scope_hash": revision["scope_hash"], "approving_user": "user",
        })
        assert approved.status_code == 200, approved.text
        approved_public = approved.json()["action"]["technical_details"]["client_engagement"]
        launched = client.post(f"/chat/client-engagements/{engagement_id}/launch", json={
            "conversation_id": conversation, "expected_state_version": approved_public["state_version"], "idempotency_key": "launch-1",
        })
        assert launched.status_code == 200, launched.text
        launched_public = launched.json()["action"]["technical_details"]["client_engagement"]
        assert launched_public["state"] == "project_launched"
        delivery_id = launched_public["project_launch"]["delivery_job_id"]
        delivery = client.get(f"/chat/projects/deliveries/{delivery_id}").json()
        assert delivery["client_engagement"]["engagement_id"] == engagement_id
        assert delivery["plan_approval"] is None
    with TestClient(create_app(database, tmp_path)) as client:
        recovered = client.post(f"/chat/client-engagements/{engagement_id}/recover", json={
            "conversation_id": conversation, "expected_state_version": launched_public["state_version"],
        })
        assert recovered.status_code == 200
        assert recovered.json()["action"]["technical_details"]["client_engagement"]["state"] == "project_launched"


def test_chat_and_stream_create_one_deduplicated_engagement_card(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "chat.db", tmp_path)) as client:
        conversation = _connect(client, project)
        response = client.post("/chat/run", json={"message": WEBSITE, "conversation_id": conversation, "use_rag": True})
        assert response.status_code == 200
        assert response.json()["action"]["action_type"] == "client_engagement"
        engagement_id = response.json()["action"]["action_id"]
        history = client.get("/chat/runs", params={"limit": 100}).json()
        serialized = json.dumps(history)
        assert "never-expose" not in serialized
        assert engagement_id in serialized


def test_audit_is_bounded_and_contains_required_lifecycle_events(tmp_path: Path) -> None:
    _service_value, repository, engagement = _website_scope(tmp_path)
    events = repository.list_client_engagement_audit_events(engagement["engagement_id"])
    operations = {item["operation"] for item in events}
    assert {"engagement_created", "evidence_authorized", "evidence_collected", "requirement_extraction_completed", "clarification_requested", "assumption_accepted", "scope_revision_created", "scope_preview_shown"} <= operations
    assert "never-expose" not in json.dumps(events)
