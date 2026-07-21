from __future__ import annotations

import json

from backend.app.project_analysis.model_synthesis import (
    CanonicalProviderProfile,
    CanonicalSynthesisOrchestrator,
    FakeSynthesisGateway,
)
from backend.app.project_artifacts import ProjectArtifactType
from backend.app.project_coordinator import ProjectCoordinatorExecutor
from backend.app.project_models import ProjectModelInvocationStatus, ProjectModelInvocationStore
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


def _executor_with_synthesis(tmp_path):
    control, artifacts, coordinator, _executor, project_id = _runtime(
        tmp_path, include_patch_operations=False
    )
    gateway = _synthesis_gateway()
    invocations = ProjectModelInvocationStore(control.database_path)
    invocations.initialize()
    orchestrator = CanonicalSynthesisOrchestrator(
        invocations=invocations, artifacts=artifacts, gateway=gateway,
    )
    profile = CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model)
    executor = ProjectCoordinatorExecutor(
        coordinator, control, artifacts, orchestrator=orchestrator, provider_profile=profile,
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
