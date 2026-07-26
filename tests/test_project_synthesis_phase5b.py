from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.database.migrations import (
    SCHEMA_MIGRATIONS,
    apply_schema_migrations,
    assert_schema_compatible,
)
from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    CapabilityStatus,
    HardwareAdmissionDecision,
    MemoryCapability,
    OllamaCapability,
    ResourceRequest,
    SchedulerStatus,
    VRAMCapability,
)
from backend.app.local_ai.generation import LocalGenerationGateway
from backend.app.local_ai.provider import ProviderGenerationResponse, ProviderInspection
from backend.app.local_ai.service import LocalAIService
from backend.app.project_analysis.model_synthesis.gateway import (
    PHASE5B_PATCH_PROMPT_VERSION,
    Phase5ALocalSynthesisGateway,
    SynthesisGatewayError,
)
from backend.app.project_analysis.model_synthesis.proposals import (
    ClarificationProposalOutput,
    CommandProposalOutput,
    DiagnosisProposalOutput,
    EvidenceTrust,
    ImplementationPlanProposalOutput,
    PatchProposalOutput,
    ProposalLifecycle,
    ProposalType,
    SemanticValidationStatus,
    SynthesisEvidenceItem,
    SynthesisProposalStore,
    SynthesisProposalStoreError,
    build_evidence_envelope,
    build_synthesis_proposal,
    validate_clarification_semantics,
    validate_command_semantics,
    validate_diagnosis_semantics,
    validate_patch_semantics,
    validate_plan_semantics,
)
from backend.app.project_control.contracts import content_hash
from tests.test_project_synthesis_orchestrator import _gateway, _runtime


def _envelope(**updates):
    values = {
        "project_run_id": "project-1",
        "workspace_id": "workspace-1",
        "objective": "Prepare one bounded advisory proposal.",
        "scope_revision_id": "scope-1",
        "plan_revision_id": "plan-1",
        "manifest_hash": "a" * 64,
        "repository_state_identity": "root-state-1",
        "evidence_identity": "evidence-1",
        "evidence_source_identity": "artifact-1",
        "evidence": {"finding": "deterministic", "paths": ["src/app.py"]},
        "allowed_paths": ("src/app.py", "tests/test_app.py"),
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(updates)
    return build_evidence_envelope(**values)


def test_versioned_evidence_envelope_is_stable_bounded_and_not_model_derived() -> None:
    first = _envelope()
    second = _envelope()
    assert first.evidence_hash == second.evidence_hash
    assert first.project_rag_enabled is False
    assert first.evidence_items[0].trust == EvidenceTrust.DETERMINISTIC
    with pytest.raises(ValidationError):
        SynthesisEvidenceItem(
            evidence_type="model_output",
            stable_identity="bad",
            source_identity="generation-1",
            content_hash=content_hash({"x": 1}),
            content={"x": 1},
            freshness_identity="state",
            trust="model_generated",
        )


def test_oversized_or_incomplete_evidence_fails_closed() -> None:
    with pytest.raises(ValidationError, match="bounded size"):
        _envelope(evidence={"content": "x" * 200_000})
    incomplete = _envelope(scan_complete=False)
    plan = _valid_plan()
    with pytest.raises(ValueError, match="scan is incomplete"):
        validate_plan_semantics(plan, incomplete)


def _valid_clarification():
    return ClarificationProposalOutput(
        proposal_type="clarification",
        project_run_id="project-1",
        questions=({
            "question": "Which exact behavior is required?",
            "reason_required": "The supplied acceptance criterion is ambiguous.",
            "blocking": True,
            "expected_answer_type": "text",
        },),
        summary="One bounded clarification is required.",
    )


def _valid_plan():
    return ImplementationPlanProposalOutput(
        proposal_type="implementation_plan",
        project_run_id="project-1",
        summary="Update source and verify it.",
        work_units=(
            {
                "work_unit_id": "work-1", "summary": "Update source.",
                "target_files": ("src/app.py",), "affected_symbols": ("run",),
                "dependencies": (), "acceptance_criteria": ("Behavior is correct.",),
                "validation_steps": ("python_compile",), "risks": (), "assumptions": (),
            },
            {
                "work_unit_id": "work-2", "summary": "Verify source.",
                "target_files": ("tests/test_app.py",), "affected_symbols": ("test_run",),
                "dependencies": ("work-1",), "acceptance_criteria": ("Tests pass.",),
                "validation_steps": ("pytest",), "risks": (), "assumptions": (),
            },
        ),
    )


def _valid_patch():
    return PatchProposalOutput(
        proposal_type="patch", project_run_id="project-1",
        summary="Modify the approved source file.",
        operations=({
            "operation": "modify", "path": "src/app.py",
            "expected_before_sha256": "b" * 64,
            "proposed_content": "VALUE = 2\n", "affected_symbols": ("VALUE",),
            "evidence_references": ("evidence-1",), "rationale": "Implement requirement.",
        },),
        validation_requirements=("python_compile",), risk="low",
    )


def _valid_command(**updates):
    values = {
        "proposal_type": "command", "project_run_id": "project-1",
        "command_category": "pytest", "argv": ("python", "-m", "pytest", "-q"),
        "working_directory_identity": "workspace-1", "purpose": "Run approved tests.",
        "timeout_seconds": 120,
    }
    values.update(updates)
    return CommandProposalOutput(**values)


def _valid_diagnosis(**updates):
    values = {
        "proposal_type": "diagnosis", "project_run_id": "project-1",
        "observed_evidence": ("evidence-1",), "probable_cause": "A bounded mismatch.",
        "confidence": 0.8, "recommended_repair_path": ("src/app.py",),
        "additional_evidence_required": (),
    }
    values.update(updates)
    return DiagnosisProposalOutput(**values)


def test_all_five_strict_proposal_contracts_and_semantics_are_supported() -> None:
    envelope = _envelope()
    clarification = _valid_clarification()
    plan = _valid_plan()
    patch = _valid_patch()
    command = _valid_command()
    diagnosis = _valid_diagnosis()
    validate_clarification_semantics(clarification, envelope)
    validate_plan_semantics(plan, envelope)
    validate_patch_semantics(patch, envelope)
    validate_command_semantics(command, envelope)
    validate_diagnosis_semantics(diagnosis, envelope)
    assert {clarification.proposal_type, plan.proposal_type, patch.proposal_type,
            command.proposal_type, diagnosis.proposal_type} == {
        "clarification", "implementation_plan", "patch", "command", "diagnosis"
    }


@pytest.mark.parametrize(
    "factory,payload",
    [
        (ClarificationProposalOutput, {"proposal_type": "clarification", "project_run_id": "project-1", "questions": [], "summary": "x", "extra": True}),
        (ImplementationPlanProposalOutput, {"proposal_type": "implementation_plan", "project_run_id": "project-1", "summary": "x"}),
        (PatchProposalOutput, {"proposal_type": "command", "project_run_id": "project-1", "summary": "x", "operations": [], "validation_requirements": [], "risk": "low"}),
    ],
)
def test_wrong_type_missing_fields_and_forbidden_fields_are_rejected(factory, payload) -> None:
    with pytest.raises(ValidationError):
        factory(**payload)


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "C:/secret", ".git/config", "unknown.py"])
def test_patch_paths_cannot_escape_protected_or_approved_scope(path: str) -> None:
    payload = _valid_patch().model_dump(mode="json")
    payload["operations"][0]["path"] = path
    if path in {"/etc/passwd", "../secret", "C:/secret"}:
        with pytest.raises(ValidationError):
            PatchProposalOutput.model_validate(payload)
    else:
        proposal = PatchProposalOutput.model_validate(payload)
        with pytest.raises(ValueError, match="protected|outside"):
            validate_patch_semantics(proposal, _envelope())


def test_plan_dependency_cycle_is_rejected() -> None:
    payload = _valid_plan().model_dump(mode="json")
    payload["work_units"][0]["dependencies"] = ["work-2"]
    plan = ImplementationPlanProposalOutput.model_validate(payload)
    with pytest.raises(ValueError, match="cycle"):
        validate_plan_semantics(plan, _envelope())


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"argv": ("python", "-m", "pytest && rm -rf x")}, "shell composition"),
        ({"argv": ("docker", "run", "image")}, "container"),
        ({"command_category": "arbitrary"}, "category"),
    ],
)
def test_command_proposals_reject_chaining_shells_containers_and_unknown_categories(updates, match) -> None:
    with pytest.raises(ValueError, match=match):
        validate_command_semantics(_valid_command(**updates), _envelope())


def test_diagnosis_requires_real_evidence_and_bounded_certainty() -> None:
    with pytest.raises(ValueError, match="outside"):
        validate_diagnosis_semantics(
            _valid_diagnosis(observed_evidence=("model-claim",)), _envelope()
        )
    with pytest.raises(ValueError, match="certainty"):
        validate_diagnosis_semantics(_valid_diagnosis(confidence=0.99), _envelope())


def test_duplicate_clarification_is_rejected() -> None:
    payload = _valid_clarification().model_dump(mode="json")
    payload["questions"].append(dict(payload["questions"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_clarification_semantics(
            ClarificationProposalOutput.model_validate(payload), _envelope()
        )


def test_orchestrator_persists_immutable_proposal_and_exact_preview_binding(tmp_path) -> None:
    gateway = _gateway()
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(tmp_path, gateway)
    from backend.app.project_analysis.model_synthesis import CanonicalProviderProfile

    outcome = orchestrator.prepare_patch(
        intent, evidence,
        CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model),
    )
    assert outcome.proposal_id and outcome.proposal_fingerprint
    proposal = orchestrator.proposals.get(outcome.proposal_id)
    preview = artifacts.get(outcome.artifact_id)
    assert proposal is not None and preview is not None
    assert proposal.semantic_validation_status == SemanticValidationStatus.ACCEPTED
    assert orchestrator.proposals.current_lifecycle(proposal.proposal_id) == ProposalLifecycle.PREVIEWED
    assert preview.payload["proposal_id"] == proposal.proposal_id
    assert preview.payload["proposal_fingerprint"] == proposal.proposal_fingerprint
    assert preview.payload["evidence_hash"] == proposal.evidence_hash
    with sqlite3.connect(artifacts.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE project_synthesis_proposals SET proposal_json = '{}'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM project_synthesis_proposals")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM project_synthesis_proposal_events")


def test_proposal_store_exact_replay_and_changed_binding_conflict(tmp_path) -> None:
    gateway = _gateway()
    orchestrator, _invocations, _artifacts, intent, evidence = _runtime(tmp_path, gateway)
    from backend.app.project_analysis.model_synthesis import CanonicalProviderProfile

    outcome = orchestrator.prepare_patch(
        intent, evidence,
        CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model),
    )
    stored = orchestrator.proposals.get(outcome.proposal_id)
    assert stored is not None
    replay, replayed = orchestrator.proposals.put(stored)
    assert replayed is True and replay.proposal_id == stored.proposal_id
    changed_values = stored.model_dump(mode="python", exclude={"proposal_fingerprint"})
    changed_values["generation_request_fingerprint"] = "f" * 64
    changed = build_synthesis_proposal(**changed_values)
    with pytest.raises(SynthesisProposalStoreError, match="changed evidence"):
        orchestrator.proposals.put(changed)


class _Provider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def inspect(self, *, timeout_seconds: int):
        return ProviderInspection("test", ("qwen-test:1.5b",))

    def generate(self, request, *, cancelled=None):
        self.calls += 1
        return ProviderGenerationResponse(
            "qwen-test:1.5b",
            self.response,
            metadata={"prompt_eval_count": 7, "eval_count": 3},
        )


def _fake_capabilities(model_tag: str):
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory", status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * 1024**3, available_bytes=12 * 1024**3, probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram", status=CapabilityStatus.AVAILABLE,
            total_bytes=4 * 1024**3, free_bytes=4 * 1024**3, probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama", status=CapabilityStatus.AVAILABLE,
            endpoint="http://127.0.0.1:11434", configured_models=(model_tag,),
            installed_models=(model_tag,), provider_reachable=True, probed_at=now,
        ),
    )


def _synthesis_service(database, configuration: LocalAIConfiguration, provider) -> LocalAIService:
    """Build the LocalAIService a production Phase5ALocalSynthesisGateway would use,
    injecting a fake provider client so no real Ollama call is made."""
    local_gateway = LocalGenerationGateway(
        database, configuration=configuration, provider_client=provider
    )
    service = LocalAIService(
        database, configuration=configuration, generation_gateway=local_gateway,
        probe=lambda: _fake_capabilities(configuration.synthesis_model),
    )
    service.initialize()
    service.capability_report(refresh=True)
    # Model profiles are disabled by default (no auto-start/auto-pull) -- an
    # explicit enable is required before any generation, exactly as the real
    # deployment must do once for "configured-local-model".
    version = service.configuration_state().configuration_version.model_profiles[
        "configured-local-model"
    ]
    service.set_model_enabled(
        "configured-local-model",
        enabled=True,
        actor_id="test-setup",
        expected_version=version,
        idempotency_key=f"enable-configured-local-model:{database}",
    )
    return service


def test_production_synthesis_adapter_uses_phase5a_and_replays_without_provider(tmp_path) -> None:
    _, _, _, intent, _ = _runtime(tmp_path, _gateway())
    database = tmp_path / "astra.db"
    configuration = LocalAIConfiguration(
        generation_enabled=True, project_synthesis_enabled=True,
        provider_type="ollama", endpoint_identity="http://127.0.0.1:11434",
        synthesis_model="qwen-test:1.5b", coder_model="qwen-test:1.5b",
        planner_model="qwen-test:1.5b", reviewer_model="qwen-test:1.5b",
        connection_timeout_seconds=2, generation_timeout_seconds=10,
        maximum_context_tokens=4096, maximum_output_tokens=512,
        allow_cpu_fallback=False, gpu_exclusive_concurrency=True,
    )
    request_payload = {
        "contract_version": "astra.project-synthesis.request.v1",
        "request_id": "synthesis-request-1", "job_id": "job-1",
        "conversation_id": "conversation-1", "folder_access_id": "folder-1",
        "project_run_id": intent.project_run_id,
        "coordinator_intent_id": intent.coordinator_intent_id,
        "root_fingerprint": "root", "analysis_id": "analysis-1", "index_version": "v1",
        "evidence": {"package_version": "astra.project-evidence.v1"},
        "output_contract": {}, "repair_context": None,
    }
    response = json.dumps({
        "contract_version": "astra.project-synthesis.response.v1",
        "request_id": "synthesis-request-1", "summary": "Safe proposal.",
        "operations": [{
            "operation": "modify", "path": "app.py", "expected_sha256": "a" * 64,
            "strategy": "complete_content", "replacements": [], "content": "VALUE = 2\n",
            "rationale": "Bounded change.", "affected_symbols": [],
            "evidence_references": ["app.py"],
        }],
        "assumptions": [], "uncertainties": [], "model_confidence": "high",
        "requires_clarification": False, "clarification_question": None,
        "recommended_validation": [],
    }, separators=(",", ":"))
    provider = _Provider(response)
    service = _synthesis_service(database, configuration, provider)
    adapter = Phase5ALocalSynthesisGateway(service, configuration)
    first = adapter.generate(json.dumps(request_payload, separators=(",", ":")))
    second = adapter.generate(json.dumps(request_payload, separators=(",", ":")))
    assert first.generation_id == second.generation_id
    assert second.replayed is True and provider.calls == 1
    assert first.request_fingerprint and first.endpoint_identity == configuration.endpoint_identity
    scheduler_job = service.scheduler_jobs()[0]
    provenance = service.provenance()[0]
    assert scheduler_job.project_run_id == request_payload["project_run_id"]
    assert (
        scheduler_job.coordinator_intent_id
        == request_payload["coordinator_intent_id"]
    )
    assert provenance.project_run_id == request_payload["project_run_id"]
    assert (
        provenance.coordinator_intent_id
        == request_payload["coordinator_intent_id"]
    )
    assert (
        provenance.configuration["response_schema"]
        == "astra.project-synthesis.response.v1"
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM local_ai_generation_invocations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM local_ai_scheduler_jobs").fetchone()[0] == 1


def test_phase5b_failure_exposes_only_bounded_schema_diagnostic(tmp_path) -> None:
    database = tmp_path / "diagnostic.db"
    apply_schema_migrations(database)
    configuration = LocalAIConfiguration(
        generation_enabled=True, project_synthesis_enabled=True,
        provider_type="ollama", endpoint_identity="http://127.0.0.1:11434",
        synthesis_model="qwen-test:1.5b", coder_model="qwen-test:1.5b",
        planner_model="qwen-test:1.5b", reviewer_model="qwen-test:1.5b",
        connection_timeout_seconds=2, generation_timeout_seconds=10,
        maximum_context_tokens=4096, maximum_output_tokens=512,
        allow_cpu_fallback=False, gpu_exclusive_concurrency=True,
    )
    provider = _Provider('{"contract_version":"astra.project-synthesis.response.v1"}')
    adapter = Phase5ALocalSynthesisGateway(
        _synthesis_service(database, configuration, provider),
        configuration,
    )
    payload = json.dumps({
        "contract_version": "astra.project-synthesis.request.v1",
        "request_id": "diagnostic-request-1",
    }, separators=(",", ":"))
    with pytest.raises(SynthesisGatewayError) as caught:
        adapter.generate(payload)
    diagnostic = caught.value.diagnostic
    assert set(diagnostic) == {
        "generation_failure_classification",
        "validation_error_location",
        "validation_error_type",
        "provider_identity",
        "exact_model_tag",
        "response_schema_identity",
        "response_schema_hash",
        "duration_ms",
        "prompt_eval_count",
        "eval_count",
    }
    assert diagnostic["generation_failure_classification"] == "target_schema_validation_failed"
    assert diagnostic["validation_error_location"]
    assert diagnostic["validation_error_type"] == "missing"
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["exact_model_tag"] == "qwen-test:1.5b"
    assert diagnostic["response_schema_identity"] == "astra.project-synthesis.response.v1"
    assert len(diagnostic["response_schema_hash"]) == 64
    assert diagnostic["prompt_eval_count"] == 7
    assert diagnostic["eval_count"] == 3


def _phase5b_configuration() -> LocalAIConfiguration:
    return LocalAIConfiguration(
        generation_enabled=True, project_synthesis_enabled=True,
        provider_type="ollama", endpoint_identity="http://127.0.0.1:11434",
        synthesis_model="qwen-test:1.5b", coder_model="qwen-test:1.5b",
        planner_model="qwen-test:1.5b", reviewer_model="qwen-test:1.5b",
        connection_timeout_seconds=2, generation_timeout_seconds=10,
        maximum_context_tokens=4096, maximum_output_tokens=512,
        allow_cpu_fallback=False, gpu_exclusive_concurrency=True,
    )


def _synthesis_request_payload() -> str:
    return json.dumps({
        "contract_version": "astra.project-synthesis.request.v1",
        "request_id": "gpu-admission-request-1",
    }, separators=(",", ":"))


def test_synthesis_generation_is_blocked_while_another_exclusive_job_holds_the_gpu(
    tmp_path,
) -> None:
    """Canonical synthesis must share one GPU-admission gate with chat generation
    (GPU Admission Unification): a job already claimed with `gpu_exclusive=True`
    (as chat generation submits) must make a concurrent synthesis call observe
    contention rather than silently racing it on a 4GB GPU."""
    database = tmp_path / "contention.db"
    apply_schema_migrations(database)
    configuration = _phase5b_configuration()
    provider = _Provider("{}")
    service = _synthesis_service(database, configuration, provider)

    gpu_resource = ResourceRequest(backend="cuda", gpu_exclusive=True, estimated_vram_bytes=1)
    admission = HardwareAdmissionDecision(
        outcome=AdmissionOutcome.GPU, reason="test setup: simulate chat generation holding the GPU",
        backend="cuda", device="gpu:0",
        estimated_required_bytes=1, available_bytes=10_000_000, safety_reserve_bytes=0,
    )
    competing = service.enqueue(
        workload_class="local_generation", resource_request=gpu_resource, admission=admission,
        idempotency_key="competing-chat-job",
    )
    claimed = service._claim_exact_scheduler_job(
        competing.job_id, worker_id="chat-worker", lease_seconds=60
    )
    assert claimed is not None and claimed.status == SchedulerStatus.CLAIMED

    adapter = Phase5ALocalSynthesisGateway(service, configuration)
    with pytest.raises(SynthesisGatewayError) as caught:
        adapter.generate(_synthesis_request_payload())
    assert caught.value.code == "gpu_busy"
    assert provider.calls == 0


def test_synthesis_generation_fails_closed_when_admission_is_blocked(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "blocked.db"
    apply_schema_migrations(database)
    configuration = _phase5b_configuration()
    provider = _Provider("{}")
    service = _synthesis_service(database, configuration, provider)
    monkeypatch.setattr(
        service,
        "admission_preview",
        lambda *_a, **_k: HardwareAdmissionDecision(
            outcome=AdmissionOutcome.BLOCKED_VRAM,
            reason="test forced admission denial",
            estimated_required_bytes=1,
            safety_reserve_bytes=0,
        ),
    )
    adapter = Phase5ALocalSynthesisGateway(service, configuration)
    with pytest.raises(SynthesisGatewayError) as caught:
        adapter.generate(_synthesis_request_payload())
    assert caught.value.code == "provider_unavailable"
    assert provider.calls == 0


def test_model_output_remains_inert_and_cannot_invoke_authorities(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no subprocess")))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no subprocess")))
    gateway = _gateway()
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(tmp_path, gateway)
    from backend.app.project_analysis.model_synthesis import CanonicalProviderProfile

    before = (tmp_path / "workspace" / "app.py").read_bytes()
    project_before = orchestrator.control.get_project(intent.project_run_id)
    events_before = orchestrator.control.list_events(intent.project_run_id)
    orchestrator.prepare_patch(
        intent, evidence,
        CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model),
    )
    assert (tmp_path / "workspace" / "app.py").read_bytes() == before
    project_after = orchestrator.control.get_project(intent.project_run_id)
    assert project_after.state_version == project_before.state_version
    assert project_after.verification_state == project_before.verification_state
    assert project_after.handoff_eligible == project_before.handoff_eligible
    assert orchestrator.control.list_events(intent.project_run_id) == events_before
    with sqlite3.connect(artifacts.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_approval_grants").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM project_worker_requests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_execution_dispatches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_manual_evidence").fetchone()[0] == 0


def test_migration_13_upgrades_version_12_and_enforces_immutable_shape(tmp_path) -> None:
    database = tmp_path / "migration-13.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:12])
    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:13])
    assert result.applied_versions == (13,)
    assert result.current_version == 13
    assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:13])
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
    assert "project_synthesis_proposals" in tables
    assert "project_synthesis_proposal_events" in tables
    assert "project_synthesis_proposals_no_update" in triggers
    assert "project_synthesis_proposal_events_no_delete" in triggers


def test_proposal_read_model_is_advisory_and_reload_safe(tmp_path) -> None:
    gateway = _gateway()
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(tmp_path, gateway)
    from backend.app.project_analysis.model_synthesis import CanonicalProviderProfile

    outcome = orchestrator.prepare_patch(
        intent,
        evidence,
        CanonicalProviderProfile(provider=gateway.provider, model_profile=gateway.model),
    )
    with TestClient(
        create_app(
            artifacts.database_path,
            workspace_root=tmp_path,
            project_synthesis_gateway=gateway,
        )
    ) as client:
        response = client.get(
            f"/chat/projects/{intent.project_run_id}/synthesis-proposals"
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["proposal_id"] == outcome.proposal_id
    assert payload["items"][0]["proposal_fingerprint"] == outcome.proposal_fingerprint
    assert payload["items"][0]["advisory_only"] is True
    assert "content" not in payload["items"][0]
