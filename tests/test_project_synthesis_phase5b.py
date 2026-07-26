from __future__ import annotations

import hashlib
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
    FakeSynthesisGateway,
    PHASE5B_PATCH_PROMPT_VERSION,
    Phase5ALocalSynthesisGateway,
    SynthesisGatewayError,
    _bounded_synthesis_response_schema,
    _model_prompt_payload,
    _priority_synthesis_context,
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
from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control.contracts import canonical_json, content_hash
from backend.app.project_retrieval.contracts import (
    RetrievalEvidenceItem,
    RetrievalPhase5BEvidence,
)
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


def _retrieval_attachment(
    artifacts,
    intent,
    *,
    evidence_count: int = 2,
) -> RetrievalPhase5BEvidence:
    scope_hash = "4" * 64
    plan_hash = "5" * 64
    authority_id = "6" * 64
    repository_state_hash = content_hash({
        "manifest_hash": intent.manifest_hash,
        "expected_project_state_version": intent.expected_project_state_version,
    })
    evidence = tuple(
        RetrievalEvidenceItem(
            evidence_id=f"retrieval-evidence-{rank}",
            chunk_id=f"retrieval-chunk-{rank}",
            source_id=f"retrieval-source-{rank}",
            relative_path="app.py",
            line_start=1,
            line_end=1,
            text=f"VALUE = {rank}\n",
            text_hash=content_hash(f"VALUE = {rank}\n"),
            source_content_hash=str(rank) * 64,
            bm25_score=1.0 / rank,
            semantic_score=0.8,
            hybrid_score=0.9,
            rerank_score=1.0 / rank,
            final_rank=rank,
            citation_label=f"RAG-{rank}",
        )
        for rank in range(1, evidence_count + 1)
    )
    request_binding = {
        "project_id": intent.project_run_id,
        "scope_hash": scope_hash,
        "plan_hash": plan_hash,
        "repository_state_hash": repository_state_hash,
        "expected_project_state_version": intent.expected_project_state_version,
    }
    retrieval_artifact = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.RETRIEVAL_EVIDENCE,
        binding=ProjectArtifactBinding(
            project_run_id=intent.project_run_id,
            plan_revision_id=intent.plan_revision_id,
            scope_revision_id=intent.scope_revision_id,
            manifest_hash=intent.manifest_hash,
            authority_hash=authority_id,
        ),
        payload={
            "request_binding": request_binding,
            "evidence": [
                item.model_dump(mode="json")
                for item in evidence
            ],
            "trust": "untrusted_retrieved_content",
            "advisory_only": True,
            "has_execution_authority": False,
            "has_approval_authority": False,
            "has_mutation_authority": False,
        },
    ))
    return RetrievalPhase5BEvidence(
        retrieval_artifact_id=retrieval_artifact.artifact_id,
        retrieval_artifact_hash=retrieval_artifact.content_hash,
        project_id=intent.project_run_id,
        scope_revision_id=intent.scope_revision_id,
        scope_hash=scope_hash,
        plan_revision_id=intent.plan_revision_id,
        plan_hash=plan_hash,
        repository_manifest_hash=intent.manifest_hash,
        repository_state_hash=repository_state_hash,
        project_state_version=intent.expected_project_state_version,
        authority_id=authority_id,
        evidence=evidence,
    )


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
            connection.execute(
                "UPDATE project_synthesis_proposals SET proposal_json = '{}'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM project_synthesis_proposals")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM project_synthesis_proposal_events")


def test_orchestrator_attaches_bounded_retrieval_to_canonical_synthesis(
    tmp_path,
) -> None:
    gateway = _gateway()
    orchestrator, invocations, artifacts, intent, evidence = _runtime(
        tmp_path,
        gateway,
    )
    retrieval = _retrieval_attachment(artifacts, intent)
    from backend.app.project_analysis.model_synthesis import (
        CanonicalProviderProfile,
    )

    outcome = orchestrator.prepare_patch(
        intent,
        evidence,
        CanonicalProviderProfile(
            provider=gateway.provider,
            model_profile=gateway.model,
        ),
        retrieval_evidence=retrieval,
    )

    invocation = invocations.list_for_project(intent.project_run_id)[0]
    request = invocation.request_payload
    assert request["project_rag_enabled"] is True
    assert request["retrieval_context"] == {
        "evidence_count": 2,
        "context_chars": sum(len(item.text) for item in retrieval.evidence),
        "maximum_evidence_count": 3,
        "maximum_context_chars": 24_000,
    }
    attached = request["evidence_envelope"]["retrieval_evidence"]
    assert attached["retrieval_artifact_id"] == retrieval.retrieval_artifact_id
    assert attached["retrieval_artifact_hash"] == (
        retrieval.retrieval_artifact_hash
    )
    assert attached["advisory_only"] is True
    assert attached["has_execution_authority"] is False
    assert attached["has_approval_authority"] is False
    assert attached["has_mutation_authority"] is False

    preview = artifacts.get(str(outcome.artifact_id))
    assert preview is not None
    assert preview.payload["project_rag_enabled"] is True
    assert preview.payload["retrieval_context"] == request["retrieval_context"]
    assert {
        (
            reference.get("artifact_id"),
            reference.get("content_hash"),
        )
        for reference in preview.evidence_references
    } >= {
        (
            retrieval.retrieval_artifact_id,
            retrieval.retrieval_artifact_hash,
        )
    }
    with sqlite3.connect(artifacts.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM project_worker_requests"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM project_execution_dispatches"
        ).fetchone()[0] == 0


def test_orchestrator_rejects_oversized_retrieval_before_generation(
    tmp_path,
) -> None:
    gateway = _gateway()
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(
        tmp_path,
        gateway,
    )
    retrieval = _retrieval_attachment(
        artifacts,
        intent,
        evidence_count=4,
    )
    from backend.app.project_analysis.model_synthesis import (
        CanonicalProviderProfile,
        CanonicalSynthesisBlocked,
    )

    with pytest.raises(CanonicalSynthesisBlocked) as caught:
        orchestrator.prepare_patch(
            intent,
            evidence,
            CanonicalProviderProfile(
                provider=gateway.provider,
                model_profile=gateway.model,
            ),
            retrieval_evidence=retrieval,
        )
    assert caught.value.code == "invalid_retrieval_evidence"
    assert gateway.call_count == 0


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        ("stale_state", "stale_retrieval_evidence"),
        ("missing_artifact", "invalid_retrieval_evidence"),
        ("changed_content", "invalid_retrieval_evidence"),
    ],
)
def test_orchestrator_rejects_unverifiable_retrieval(
    tmp_path,
    mutation: str,
    expected_code: str,
) -> None:
    gateway = _gateway()
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(
        tmp_path,
        gateway,
    )
    retrieval = _retrieval_attachment(artifacts, intent)
    payload = retrieval.model_dump(mode="json")
    if mutation == "stale_state":
        payload["project_state_version"] += 1
    elif mutation == "missing_artifact":
        payload["retrieval_artifact_id"] = "missing-retrieval-artifact"
    else:
        changed = dict(payload["evidence"][0])
        changed["text"] = "UNVERIFIED = True\n"
        changed["text_hash"] = content_hash(changed["text"])
        payload["evidence"][0] = changed
    retrieval = RetrievalPhase5BEvidence.model_validate(payload)
    from backend.app.project_analysis.model_synthesis import (
        CanonicalProviderProfile,
        CanonicalSynthesisBlocked,
    )

    with pytest.raises(CanonicalSynthesisBlocked) as caught:
        orchestrator.prepare_patch(
            intent,
            evidence,
            CanonicalProviderProfile(
                provider=gateway.provider,
                model_profile=gateway.model,
            ),
            retrieval_evidence=retrieval,
        )
    assert caught.value.code == expected_code
    assert gateway.call_count == 0


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
        "validation_error_reason",
        "provider_error_code",
        "provider_http_status",
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
    assert diagnostic["validation_error_reason"] is None
    assert diagnostic["provider_error_code"] is None
    assert diagnostic["provider_http_status"] is None
    assert diagnostic["provider_identity"] == "ollama"
    assert diagnostic["exact_model_tag"] == "qwen-test:1.5b"
    assert diagnostic["response_schema_identity"] == "astra.project-synthesis.response.v1"
    assert len(diagnostic["response_schema_hash"]) == 64
    assert diagnostic["prompt_eval_count"] == 7
    assert diagnostic["eval_count"] == 3


def test_synthesis_response_schema_rejects_empty_operation_evidence() -> None:
    from backend.app.project_analysis.model_synthesis.contracts import (
        SynthesisResponse,
    )

    schema = SynthesisResponse.model_json_schema()
    operation_variants = schema["$defs"]["ModifyOperation"]["properties"]
    assert operation_variants["evidence_references"]["minItems"] == 1

    with pytest.raises(ValueError):
        SynthesisResponse.model_validate({
            "contract_version": "astra.project-synthesis.response.v1",
            "request_id": "request-1",
            "summary": "Unsafe uncited proposal.",
            "operations": [{
                "operation": "modify",
                "path": "app.py",
                "expected_sha256": "a" * 64,
                "strategy": "complete_content",
                "replacements": [],
                "content": "VALUE = 2\n",
                "rationale": "Change the value.",
                "affected_symbols": ["VALUE"],
                "evidence_references": [],
            }],
            "assumptions": [],
            "uncertainties": [],
            "model_confidence": "high",
            "requires_clarification": False,
            "clarification_question": None,
            "recommended_validation": [],
        })


def test_request_bounded_schema_omits_unauthorized_operations_and_paths() -> None:
    bounded = _bounded_synthesis_response_schema({
        "evidence": {
            "allowed_modify_paths": ["app/services/pricing.py"],
            "allowed_create_paths": [],
            "allowed_delete_paths": [],
            "file_identities": {"app/services/pricing.py": "a" * 64},
            "source_excerpts": [{
                "path": "app/services/pricing.py",
                "sha256": "a" * 64,
                "text": "VALUE = 1\n",
            }],
        },
        "evidence_envelope": {
            "retrieval_evidence": {
                "evidence": [{
                    "relative_path": "app/services/pricing.py",
                    "text": "VALUE = 1",
                }]
            }
        },
    })
    schema = bounded.model_json_schema()
    assert "BoundedModifyExactOperation0" in schema["$defs"]
    assert "BoundedModifyCompleteOperation0" not in schema["$defs"]
    assert not any("CreateOperation" in name for name in schema["$defs"])
    assert not any("DeleteOperation" in name for name in schema["$defs"])
    properties = schema["$defs"]["BoundedModifyExactOperation0"]["properties"]
    path_schema = properties["path"]
    assert path_schema["const"] == "app/services/pricing.py"
    assert properties["expected_sha256"]["const"] == "a" * 64
    assert properties["strategy"]["const"] == "exact_replacements"
    assert properties["replacements"]["minItems"] == 1
    assert properties["replacements"]["maxItems"] == 3
    assert properties["rationale"]["const"] == (
        "Apply the minimal evidence-backed repair."
    )
    replacement_schema = schema["$defs"]["BoundedExactReplacement0"]
    assert replacement_schema["properties"]["expected_text"]["const"] == (
        "VALUE = 1\n"
    )

    valid = {
        "contract_version": "astra.project-synthesis.response.v1",
        "request_id": "request-1",
        "summary": "Evidence-backed bounded patch.",
        "operations": [{
            "operation": "modify",
            "path": "app/services/pricing.py",
            "expected_sha256": "a" * 64,
            "strategy": "exact_replacements",
            "replacements": [{
                "start_line": 1,
                "end_line": 1,
                "expected_text": "VALUE = 1\n",
                "replacement_text": "VALUE = 2\n",
            }],
            "content": None,
            "rationale": "Apply the minimal evidence-backed repair.",
            "affected_symbols": ["VALUE"],
            "evidence_references": ["app/services/pricing.py"],
        }],
        "assumptions": [],
        "uncertainties": [],
        "model_confidence": "high",
        "requires_clarification": False,
        "clarification_question": None,
        "recommended_validation": [],
    }
    assert bounded.model_validate(valid).operations[0].operation == "modify"
    with pytest.raises(ValueError):
        bounded.model_validate({
            **valid,
            "operations": [{
                **valid["operations"][0],
                "operation": "create",
                "path": "./app/services/pricing.py",
                "expected_sha256": "missing",
            }],
        })


def test_priority_context_puts_current_failure_and_source_first() -> None:
    priority = _priority_synthesis_context({
        "evidence": {
            "work_unit": {"summary": "Fix line_total."},
            "allowed_modify_paths": ["app/services/pricing.py"],
            "allowed_create_paths": [],
            "allowed_delete_paths": [],
            "file_identities": {"app/services/pricing.py": "a" * 64},
            "failure_evidence": {
                "status": "failed",
                "failing_tests": ["tests/test_pricing.py"],
                "assertions": [{"expected_hint": "21", "actual_hint": "10"}],
                "error_types": ["AssertionError"],
                "output_tail": "x" * 2_000,
            },
        },
        "evidence_envelope": {
            "retrieval_evidence": {
                "evidence": [{
                    "relative_path": "app/services/pricing.py",
                    "line_start": 1,
                    "line_end": 12,
                    "text": "def line_total(item):\n    return item.price + item.quantity\n",
                    "citation_label": "RAG-1",
                }]
            }
        },
    })

    assert priority["work_unit"]["summary"] == "Fix line_total."
    assert priority["failure_evidence"]["failing_tests"] == [
        "tests/test_pricing.py"
    ]
    assert len(priority["failure_evidence"]["output_tail"]) == 1_600
    assert priority["retrieved_source_evidence"][0]["path"] == (
        "app/services/pricing.py"
    )
    assert priority["retrieved_source_evidence"][0]["exact_lines"][0] == {
        "line": 1,
        "text": "def line_total(item):\n",
    }
    assert priority["repair_directive"]["requirements"].endswith(
        "replacement_text must differ from expected_text."
    )


def test_model_prompt_view_retains_bindings_without_duplicate_bodies() -> None:
    payload = {
        "project_run_id": "project-1",
        "coordinator_intent_id": "intent-1",
        "evidence_artifact_hash": "e" * 64,
        "evidence": {"work_unit": {"summary": "Fix it."}},
        "evidence_envelope": {
            "schema_version": "astra.project-synthesis.evidence-envelope.v1",
            "evidence_envelope_id": "envelope-1",
            "project_run_id": "project-1",
            "scope_revision_id": "scope-1",
            "plan_revision_id": "plan-1",
            "repository_manifest_identity": "a" * 64,
            "repository_state_identity": "state-1",
            "evidence_hash": "b" * 64,
            "project_rag_enabled": True,
            "evidence_items": [{
                "stable_identity": "evidence-1",
                "source_identity": "artifact-1",
                "content_hash": "c" * 64,
                "content": {"large": "x" * 60_000},
                "freshness_identity": "fresh-1",
                "trust": "repository_data",
            }],
            "retrieval_evidence": {
                "retrieval_artifact_id": "retrieval-1",
                "retrieval_artifact_hash": "d" * 64,
                "evidence": [{"text": "duplicated source"}],
            },
        },
    }

    view = _model_prompt_payload(payload)

    assert view["project_run_id"] == "project-1"
    assert view["coordinator_intent_id"] == "intent-1"
    assert view["evidence"] == {
        "model_prompt_view": "current_task_priority_block_above",
        "full_evidence_bound_by": "e" * 64,
    }
    compact = view["evidence_envelope"]
    assert compact["evidence_hash"] == "b" * 64
    assert compact["evidence_references"][0]["content_hash"] == "c" * 64
    assert compact["retrieval_reference"]["retrieval_artifact_id"] == (
        "retrieval-1"
    )
    assert "evidence_items" not in compact
    assert len(canonical_json(view)) < 5_000


def test_orchestrator_rejects_no_effect_content_before_preview(tmp_path) -> None:
    def unchanged_response(raw_request: str) -> str:
        request = json.loads(raw_request)
        return json.dumps({
            "contract_version": "astra.project-synthesis.response.v1",
            "request_id": request["request_id"],
            "summary": "Return unchanged content.",
            "operations": [{
                "operation": "modify",
                "path": "app.py",
                "expected_sha256": hashlib.sha256(b"VALUE = 1\n").hexdigest(),
                "strategy": "complete_content",
                "replacements": [],
                "content": "VALUE = 1\n",
                "rationale": "No effective repair.",
                "affected_symbols": ["VALUE"],
                "evidence_references": ["app.py"],
            }],
            "assumptions": [],
            "uncertainties": [],
            "model_confidence": "low",
            "requires_clarification": False,
            "clarification_question": None,
            "recommended_validation": [],
        })

    gateway = FakeSynthesisGateway(response=unchanged_response)
    orchestrator, _invocations, artifacts, intent, evidence = _runtime(
        tmp_path,
        gateway,
    )
    from backend.app.project_analysis.model_synthesis import (
        CanonicalProviderProfile,
        CanonicalSynthesisBlocked,
    )

    with pytest.raises(CanonicalSynthesisBlocked) as caught:
        orchestrator.prepare_patch(
            intent,
            evidence,
            CanonicalProviderProfile(
                provider=gateway.provider,
                model_profile=gateway.model,
            ),
        )

    assert caught.value.code == "no_effect_patch"
    assert not any(
        artifact.artifact_type == ProjectArtifactType.PATCH_PREVIEW
        for artifact in artifacts.list_for_project(intent.project_run_id)
    )


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
    assert caught.value.code == "insufficient_vram"
    assert caught.value.diagnostic == {
        "admission_outcome": "blocked_due_to_vram",
        "provider_readiness_reason": "test forced admission denial",
        "estimated_required_bytes": 1,
        "available_bytes": None,
        "safety_reserve_bytes": 0,
        "admission_backend": None,
        "admission_device": None,
        "admitted_context": None,
    }
    assert provider.calls == 0


def test_synthesis_gateway_propagates_configured_cpu_fallback_to_admission(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "cpu-fallback.db"
    apply_schema_migrations(database)
    configuration = _phase5b_configuration().model_copy(
        update={"allow_cpu_fallback": True}
    )
    provider = _Provider("{}")
    service = _synthesis_service(database, configuration, provider)
    observed: dict[str, bool] = {}

    def blocked_admission(request, *, report=None):
        del report
        observed["allow_cpu_fallback"] = request.allow_cpu_fallback
        return HardwareAdmissionDecision(
            outcome=AdmissionOutcome.BLOCKED_RAM,
            reason="test forced RAM admission denial",
            estimated_required_bytes=2,
            available_bytes=1,
            safety_reserve_bytes=0,
        )

    monkeypatch.setattr(service, "admission_preview", blocked_admission)
    adapter = Phase5ALocalSynthesisGateway(service, configuration)
    with pytest.raises(SynthesisGatewayError) as caught:
        adapter.generate(_synthesis_request_payload())

    assert observed == {"allow_cpu_fallback": True}
    assert caught.value.code == "insufficient_memory"
    assert caught.value.diagnostic["admission_outcome"] == "blocked_due_to_ram"
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
