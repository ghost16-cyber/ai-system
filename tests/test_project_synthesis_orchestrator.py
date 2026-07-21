from __future__ import annotations

import json

import pytest

from backend.app.project_analysis.model_synthesis import (
    CanonicalProviderProfile, CanonicalSynthesisBlocked, CanonicalSynthesisOrchestrator,
    FakeSynthesisGateway, UnavailableSynthesisGateway,
)
from backend.app.project_artifacts import (
    ProjectArtifactBinding, ProjectArtifactStore, ProjectArtifactType, build_project_artifact,
)
from backend.app.project_control import ProjectCommand, ProjectCommandType, ProjectControlPlane
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_models import ProjectModelInvocationStatus, ProjectModelInvocationStore


def _runtime(tmp_path, gateway):
    database = tmp_path / "astra.db"
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    project = CanonicalProjectService(control, artifacts).create_project(
        conversation_id="conversation-1", workspace_id="workspace-1",
        repository_root=root, repository_root_fingerprint="root-fingerprint",
        actor_id="local-user", idempotency_key="create",
        folder_authority={
            "status": "completed", "action_id": "workspace-1", "conversation_id": "conversation-1",
            "workspace_id": "workspace-1", "repository_root_fingerprint": "root-fingerprint",
        },
        specification={"specification_hash": "1" * 64, "included_paths": ["app.py"]},
        manifest={"manifest_hash": "2" * 64, "complete": True},
        plan={
            "acceptance_criteria": [{"criterion_id": "criterion-1", "required": True}],
            "work_units": [{"work_unit_id": "work-1", "expected_files": ["app.py"]}],
        },
    )
    run = control.get_project(project.project_run_id)
    control.execute(ProjectCommand(
        command_type=ProjectCommandType.APPROVE_PLAN, project_run_id=run.project_run_id,
        conversation_id=run.conversation_id, workspace_id=run.workspace_id,
        repository_root=run.repository_root, repository_root_fingerprint=run.repository_root_fingerprint,
        actor_id=run.actor_id, expected_state_version=run.state_version, idempotency_key="approve-plan",
        plan_revision_id=run.current_plan_revision_id, scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash, authority_scope={"operation": "prepare_work_units"},
    ))
    coordinator = ProjectCoordinatorService(database, control)
    coordinator.initialize()
    intent = coordinator.reconcile(project.project_run_id)
    assert intent is not None
    evidence = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.COORDINATOR_DECISION,
        binding=ProjectArtifactBinding(
            project_run_id=intent.project_run_id, plan_revision_id=intent.plan_revision_id,
            scope_revision_id=intent.scope_revision_id, manifest_hash=intent.manifest_hash,
            coordinator_intent_id=intent.coordinator_intent_id,
        ),
        payload={
            "evidence": {
                "allowed_modify_paths": ["app.py"], "allowed_create_paths": [],
                "allowed_delete_paths": [], "work_unit": {"expected_files": ["app.py"]},
                "requirements": ["Change the value"], "project_rag_enabled": False,
            }
        },
    ))
    invocations = ProjectModelInvocationStore(database)
    invocations.initialize()
    orchestrator = CanonicalSynthesisOrchestrator(
        invocations=invocations, artifacts=artifacts, gateway=gateway,
    )
    return orchestrator, invocations, artifacts, intent, evidence


def _gateway():
    def response(payload: str) -> str:
        request = json.loads(payload)
        return json.dumps({
            "contract_version": "astra.project-synthesis.response.v1",
            "request_id": request["request_id"], "summary": "Update the bounded source file.",
            "operations": [{
                "operation": "modify", "path": "app.py", "expected_sha256": "3" * 64,
                "strategy": "complete_content", "replacements": [], "content": "VALUE = 2\n",
                "rationale": "Implements the approved requirement.", "affected_symbols": ["VALUE"],
                "evidence_references": ["app.py"],
            }],
            "assumptions": [], "uncertainties": [], "model_confidence": "high",
            "requires_clarification": False, "clarification_question": None,
            "recommended_validation": [],
        }, separators=(",", ":"))
    return FakeSynthesisGateway(response=response)


def test_canonical_synthesis_is_durable_provider_neutral_and_exactly_once(tmp_path) -> None:
    gateway = _gateway()
    orchestrator, invocations, artifacts, intent, evidence = _runtime(tmp_path, gateway)
    profile = CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model)
    first = orchestrator.prepare_patch(intent, evidence, profile)
    second = orchestrator.prepare_patch(intent, evidence, profile)
    assert first.status == "prepared"
    assert second.replayed is True
    assert second.artifact_id == first.artifact_id
    assert gateway.call_count == 1
    stored = invocations.list_for_project(intent.project_run_id)
    assert len(stored) == 1 and stored[0].status == ProjectModelInvocationStatus.SUCCEEDED
    preview = artifacts.get(str(first.artifact_id))
    assert preview is not None and preview.artifact_type == ProjectArtifactType.PATCH_PREVIEW
    assert preview.payload["requires_exact_approval"] is True
    assert preview.payload["project_rag_enabled"] is False


def test_provider_unavailability_is_a_durable_typed_block(tmp_path) -> None:
    gateway = UnavailableSynthesisGateway()
    orchestrator, invocations, _artifacts, intent, evidence = _runtime(tmp_path, gateway)
    profile = CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model)
    with pytest.raises(CanonicalSynthesisBlocked) as caught:
        orchestrator.prepare_patch(intent, evidence, profile)
    assert caught.value.code == "provider_unavailable"
    stored = invocations.list_for_project(intent.project_run_id)
    assert len(stored) == 1 and stored[0].status == ProjectModelInvocationStatus.FAILED
    with pytest.raises(CanonicalSynthesisBlocked):
        orchestrator.prepare_patch(intent, evidence, profile)


def test_stale_evidence_binding_and_out_of_scope_paths_fail_closed(tmp_path) -> None:
    gateway = _gateway()
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(tmp_path, gateway)
    stale = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.COORDINATOR_DECISION,
        binding=ProjectArtifactBinding(
            project_run_id=intent.project_run_id, plan_revision_id=intent.plan_revision_id,
            scope_revision_id=intent.scope_revision_id, manifest_hash="9" * 64,
        ), payload={"evidence": {"allowed_modify_paths": ["app.py"]}},
    ))
    profile = CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model)
    with pytest.raises(CanonicalSynthesisBlocked, match="stale"):
        orchestrator.prepare_patch(intent, stale, profile)
    assert artifacts.get(evidence.artifact_id) is not None
