from __future__ import annotations

import json
from pathlib import Path

from backend.app.project_analysis.model_synthesis import (
    CanonicalProviderProfile,
    CanonicalSynthesisOrchestrator,
    FakeSynthesisGateway,
)
from backend.app.project_artifacts import ProjectArtifactType
from backend.app.project_coordinator import ProjectCoordinatorExecutor
from backend.app.project_models import ProjectModelInvocationStatus, ProjectModelInvocationStore
from backend.app.project_retrieval import ProjectRetrievalService
from backend.app.project_retrieval.bindings import (
    canonical_retrieval_authority_id,
)
from backend.app.project_retrieval.contracts import CorpusIngestionRequest
from tests.test_project_coordinator_execution import _runtime
from tests.test_project_repair_coordinator import _record_initial_domain_failure


def _synthesis_gateway():
    def response(payload: str) -> str:
        request = json.loads(payload)
        return json.dumps({
            "contract_version": "astra.project-synthesis.response.v1",
            "request_id": request["request_id"],
            "summary": "Bounded synthesized patch within canonical evidence.",
            "operations": [{
                "operation": "modify", "path": "src/app.py", "expected_sha256": "3" * 64,
                "strategy": "complete_content", "replacements": [], "content": "VALUE = 2\n",
                "rationale": "Implements the approved requirement.",
                "affected_symbols": ["VALUE"], "evidence_references": ["src/app.py"],
            }],
            "assumptions": [], "uncertainties": [], "model_confidence": "high",
            "requires_clarification": False, "clarification_question": None,
            "recommended_validation": [],
        }, separators=(",", ":"))
    return FakeSynthesisGateway(response=response)


def _executor_with_synthesis(
    tmp_path,
    *,
    with_retrieval: bool = False,
    ingest_retrieval: bool = True,
):
    control, artifacts, coordinator, _executor, project_id = _runtime(
        tmp_path, include_patch_operations=False
    )
    gateway = _synthesis_gateway()
    invocations = ProjectModelInvocationStore(control.database_path)
    invocations.initialize()
    orchestrator = CanonicalSynthesisOrchestrator(
        invocations=invocations, artifacts=artifacts, gateway=gateway,
    )
    retrieval = None
    if with_retrieval:
        retrieval = ProjectRetrievalService(
            control.database_path,
            control,
            artifacts,
        )
        retrieval.initialize()
        if ingest_retrieval:
            run = control.get_project(project_id)
            scope = control.get_scope_revision(run.current_scope_revision_id)
            plan = control.get_plan_revision(run.current_plan_revision_id)
            repository_state_hash = retrieval.compute_repository_state(
                Path(run.repository_root),
                scope.included_paths,
                scope.excluded_paths,
            )
            retrieval.ingest_project_corpus(CorpusIngestionRequest(
                project_id=run.project_run_id,
                conversation_id=run.conversation_id,
                actor_id=run.actor_id,
                workspace_id=run.workspace_id,
                repository_root=run.repository_root,
                scope_revision_id=scope.scope_revision_id,
                scope_hash=scope.content_hash,
                plan_revision_id=plan.plan_revision_id,
                plan_hash=plan.content_hash,
                repository_manifest_hash=run.current_manifest_hash,
                repository_state_hash=repository_state_hash,
                expected_project_state_version=run.state_version,
                authority_id=canonical_retrieval_authority_id(run),
                idempotency_key="coordinator-synthesis-ingestion",
            ))
    profile = CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model)
    executor = ProjectCoordinatorExecutor(
        coordinator,
        control,
        artifacts,
        orchestrator=orchestrator,
        provider_profile=profile,
        retrieval=retrieval,
    )
    return control, artifacts, coordinator, executor, project_id, gateway, invocations


def test_coordinator_uses_durable_synthesis_when_no_deterministic_patch(tmp_path) -> None:
    control, artifacts, _coordinator, executor, project_id, gateway, invocations = (
        _executor_with_synthesis(tmp_path)
    )

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    previews = artifacts.list_for_project(
        project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW
    )
    assert len(previews) == 1
    assert previews[0].payload.get("requires_exact_approval") is True
    assert previews[0].payload.get("project_rag_enabled") is False

    # The provider was invoked exactly once through the durable invocation store.
    stored = invocations.list_for_project(project_id)
    assert len(stored) == 1
    assert stored[0].status == ProjectModelInvocationStatus.SUCCEEDED
    assert gateway.call_count == 1

    # No further work is emitted for the completed intent.
    assert executor.run_once("coordinator-worker") is False


def test_no_orchestrator_keeps_deterministic_only_blocking(tmp_path) -> None:
    control, artifacts, coordinator, _executor, project_id = _runtime(
        tmp_path, include_patch_operations=False
    )
    executor = ProjectCoordinatorExecutor(coordinator, control, artifacts)

    # Without an injected orchestrator, a missing deterministic patch does not
    # synthesize and no preview is created.
    executor.run_once("coordinator-worker")
    assert artifacts.list_for_project(
        project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW
    ) == []


def test_coordinator_attaches_ready_project_retrieval_to_synthesis(
    tmp_path,
) -> None:
    control, artifacts, _coordinator, executor, project_id, gateway, invocations = (
        _executor_with_synthesis(tmp_path, with_retrieval=True)
    )

    assert executor.run_once("coordinator-worker") is True

    preview = artifacts.list_for_project(
        project_id,
        artifact_type=ProjectArtifactType.PATCH_PREVIEW,
    )[0]
    assert preview.payload["project_rag_enabled"] is True
    assert preview.payload["retrieval_context"]["evidence_count"] == 1
    assert preview.payload["retrieval_context"]["maximum_evidence_count"] == 3
    stored = invocations.list_for_project(project_id)
    assert len(stored) == 1
    request = stored[0].request_payload
    assert request["project_rag_enabled"] is True
    assert request["evidence"]["project_rag"]["status"] == "attached"
    assert (
        request["evidence_envelope"]["retrieval_evidence"]["advisory_only"]
        is True
    )
    assert gateway.call_count == 1
    run = control.get_project(project_id)
    assert run.pending_user_action
    assert run.pending_user_action.startswith("approve_patch:")


def test_coordinator_preserves_synthesis_when_retrieval_corpus_is_not_ready(
    tmp_path,
) -> None:
    _control, artifacts, _coordinator, executor, project_id, gateway, invocations = (
        _executor_with_synthesis(
            tmp_path,
            with_retrieval=True,
            ingest_retrieval=False,
        )
    )

    assert executor.run_once("coordinator-worker") is True

    preview = artifacts.list_for_project(
        project_id,
        artifact_type=ProjectArtifactType.PATCH_PREVIEW,
    )[0]
    assert preview.payload["project_rag_enabled"] is False
    request = invocations.list_for_project(project_id)[0].request_payload
    assert request["project_rag_enabled"] is False
    assert request["evidence"]["project_rag"] == {
        "attempted": False,
        "status": "corpus_not_ready",
        "invalidated": False,
        "active_chunk_count": 0,
        "advisory_only": True,
    }
    assert gateway.call_count == 1


def test_repair_coordinator_uses_the_same_durable_orchestrator(tmp_path) -> None:
    control, artifacts, coordinator, _executor, _repair, project_id = (
        _record_initial_domain_failure(
            tmp_path, include_repair_operations=False
        )
    )
    gateway = _synthesis_gateway()
    invocations = ProjectModelInvocationStore(control.database_path)
    invocations.initialize()
    orchestrator = CanonicalSynthesisOrchestrator(
        invocations=invocations, artifacts=artifacts, gateway=gateway,
    )
    executor = ProjectCoordinatorExecutor(
        coordinator,
        control,
        artifacts,
        orchestrator=orchestrator,
        provider_profile=CanonicalProviderProfile(
            provider=gateway.provider, model_profile=gateway.model
        ),
    )

    assert executor.run_once("coordinator-worker") is True
    run = control.get_project(project_id)
    assert run.repair_state["status"] == "awaiting_approval"
    assert run.current_artifact_ids.get("repair_preview")
    stored = invocations.list_for_project(project_id)
    assert len(stored) == 1
    assert stored[0].status == ProjectModelInvocationStatus.SUCCEEDED
    assert gateway.call_count == 1
