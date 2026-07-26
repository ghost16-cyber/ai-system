#!/usr/bin/env python3
"""Explicit, disposable Phase 5B real-model smoke check.

This script never starts Ollama, pulls a model, executes a command, invokes a
worker, or touches a user project. It only asks an already configured local
provider for one advisory patch proposal in a temporary workspace/database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import CapabilityStatus, OllamaCapability
from backend.app.local_ai.provider import ProviderClientError
from backend.app.project_analysis.model_synthesis import (
    CanonicalProviderProfile,
    CanonicalSynthesisBlocked,
    CanonicalSynthesisOrchestrator,
    UnavailableSynthesisGateway,
    build_synthesis_gateway_from_environment,
)
from backend.app.project_analysis.model_synthesis.gateway import (
    SYNTHESIS_MODEL_PROFILE_ID,
    SynthesisGatewayError,
)
from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import ProjectCommand, ProjectCommandType, ProjectControlPlane
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_models import ProjectModelInvocationStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


_SMOKE_DIAGNOSTIC_FIELDS = (
    "generation_failure_classification",
    "provider_readiness_reason",
    "provider_reachable",
    "configured_model_missing",
    "provider_error_code",
    "provider_http_status",
    "readiness_error_type",
    "readiness_stage",
    "admission_outcome",
    "estimated_required_bytes",
    "available_bytes",
    "safety_reserve_bytes",
    "admission_backend",
    "admission_device",
    "admitted_context",
    "validation_error_location",
    "validation_error_type",
    "validation_error_reason",
    "provider_identity",
    "exact_model_tag",
    "response_schema_identity",
    "response_schema_hash",
    "duration_ms",
    "prompt_eval_count",
    "eval_count",
)


def _readiness_diagnostic(gateway) -> dict[str, object]:
    return {
        "provider_identity": gateway.provider,
        "exact_model_tag": gateway.model,
        "response_schema_identity": "astra.project-synthesis.response.v1",
        "provider_readiness_reason": None,
        "provider_reachable": None,
        "configured_model_missing": None,
        "provider_error_code": None,
        "provider_http_status": None,
        "readiness_error_type": None,
        "readiness_stage": None,
    }


def _safe_readiness_failure(
    exc: Exception, *, stage: str
) -> tuple[str, dict[str, object]]:
    details: dict[str, object] = {
        "readiness_error_type": type(exc).__name__[:120],
        "readiness_stage": stage,
        "provider_readiness_reason": f"{stage}_failed",
    }
    if isinstance(exc, ProviderClientError):
        details.update({
            "provider_error_code": exc.code.value,
            "provider_http_status": exc.diagnostic.get("http_status"),
            "provider_readiness_reason": " ".join(exc.safe_message.split())[:300],
        })
        return "provider_unavailable", details
    safe_value_errors = {
        "model_not_locally_available": "model_unavailable",
        "model_profile_not_enabled": "model_unavailable",
        "model profile not found": "model_unavailable",
        "stale_configuration_version": "disposable_profile_enablement_failed",
    }
    if isinstance(exc, ValueError) and str(exc) in safe_value_errors:
        details["provider_readiness_reason"] = str(exc)
        return safe_value_errors[str(exc)], details
    return (
        "readiness_inspection_failed"
        if stage == "capability_inspection"
        else "disposable_profile_enablement_failed"
    ), details


def _enable_disposable_model_profile(gateway) -> dict[str, object]:
    """Enable only the confirmed model in this script's temporary database."""
    service = gateway.local_ai_service
    readiness_diagnostic = _readiness_diagnostic(gateway)
    try:
        report = service.capability_report(refresh=True)
    except Exception as exc:
        code, details = _safe_readiness_failure(
            exc, stage="capability_inspection"
        )
        readiness_diagnostic.update(details)
        raise SynthesisGatewayError(
            "The disposable smoke check could not inspect local-model readiness.",
            code=code,
            diagnostic=readiness_diagnostic,
        ) from exc
    ollama_capability = next(
        (
            capability
            for capability in report.capabilities
            if isinstance(capability, OllamaCapability)
        ),
        None,
    )
    if ollama_capability is None:
        readiness_diagnostic["provider_readiness_reason"] = (
            "provider_capability_missing"
        )
    else:
        readiness_diagnostic.update({
            "provider_readiness_reason": ollama_capability.reason,
            "provider_reachable": ollama_capability.provider_reachable,
            "configured_model_missing": (
                ollama_capability.configured_model_missing
            ),
        })
    configured = next(
        (
            model
            for model in service.models()
            if model.model_profile_id == SYNTHESIS_MODEL_PROFILE_ID
        ),
        None,
    )
    if configured is None:
        readiness_diagnostic["provider_readiness_reason"] = (
            "configured_model_profile_missing"
        )
        raise SynthesisGatewayError(
            "The configured local model profile is not registered.",
            code="model_unavailable",
            diagnostic=readiness_diagnostic,
        )
    if not configured.local_available:
        provider_states = {
            CapabilityStatus.NOT_CONFIGURED,
            CapabilityStatus.PROVIDER_UNREACHABLE,
        }
        raise SynthesisGatewayError(
            "The configured local model is not ready for the disposable smoke check.",
            code=(
                "provider_unavailable"
                if configured.policy_status in provider_states
                else "model_unavailable"
            ),
            diagnostic=readiness_diagnostic,
        )
    try:
        service.set_model_enabled(
            SYNTHESIS_MODEL_PROFILE_ID,
            enabled=True,
            actor_id="phase5b-smoke-user",
            expected_version=configured.configuration_version,
            idempotency_key="phase5b-smoke-enable-disposable-model",
        )
    except Exception as exc:
        code, details = _safe_readiness_failure(
            exc, stage="disposable_profile_enablement"
        )
        readiness_diagnostic.update(details)
        raise SynthesisGatewayError(
            "The disposable model profile could not be enabled safely.",
            code=code,
            diagnostic=readiness_diagnostic,
        ) from exc
    return readiness_diagnostic


def _smoke_failure_diagnostic(exc: Exception, configuration) -> dict[str, object]:
    diagnostic = {
        "provider_identity": configuration.provider_type,
        "exact_model_tag": configuration.synthesis_model,
        "response_schema_identity": "astra.project-synthesis.response.v1",
    }
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (SynthesisGatewayError, CanonicalSynthesisBlocked)):
            diagnostic.update(current.diagnostic)
            diagnostic["generation_failure_classification"] = current.code
            diagnostic.setdefault(
                "provider_readiness_reason",
                " ".join(str(current).split())[:300],
            )
            return diagnostic
        current = current.__cause__ or current.__context__
    diagnostic.update({
        "generation_failure_classification": "smoke_internal_failure",
        "provider_readiness_reason": "unexpected_smoke_failure",
        "readiness_error_type": type(exc).__name__[:120],
    })
    return diagnostic


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one explicit, disposable Phase 5B advisory synthesis smoke check."
    )
    parser.add_argument(
        "--confirm-advisory-generation",
        action="store_true",
        help="Confirm that one request may be sent to an already-running configured provider.",
    )
    args = parser.parse_args()
    if not args.confirm_advisory_generation:
        parser.error("--confirm-advisory-generation is required")

    configuration = load_local_ai_configuration()
    if not configuration.generation_enabled or not configuration.project_synthesis_enabled:
        print(
            "blocked: set ASTRA_LOCAL_AI_GENERATION_ENABLED=1 and "
            "ASTRA_PROJECT_SYNTHESIS_ENABLED=1 explicitly",
            file=sys.stderr,
        )
        return 2

    readiness_diagnostic: dict[str, object] = {
        "provider_identity": configuration.provider_type,
        "exact_model_tag": configuration.synthesis_model,
        "response_schema_identity": "astra.project-synthesis.response.v1",
        "provider_readiness_reason": None,
        "provider_reachable": None,
        "configured_model_missing": None,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="astra-phase5b-smoke-") as temporary:
            root = Path(temporary)
            workspace = root / "disposable-project"
            workspace.mkdir()
            source = workspace / "app.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            before_hash = _sha256(source)
            database = root / "astra-smoke.db"

            artifacts = ProjectArtifactStore(database)
            control = ProjectControlPlane(database, artifact_store=artifacts)
            control.initialize()
            artifacts.initialize()
            project = CanonicalProjectService(control, artifacts).create_project(
                conversation_id="phase5b-smoke-conversation",
                workspace_id="phase5b-smoke-workspace",
                repository_root=workspace,
                repository_root_fingerprint=before_hash,
                actor_id="phase5b-smoke-user",
                idempotency_key="phase5b-smoke-create",
                folder_authority={
                    "status": "completed",
                    "action_id": "phase5b-smoke-workspace",
                    "conversation_id": "phase5b-smoke-conversation",
                    "workspace_id": "phase5b-smoke-workspace",
                    "repository_root": str(workspace.resolve()),
                    "repository_root_fingerprint": before_hash,
                },
                specification={
                    "specification_hash": hashlib.sha256(b"phase5b-smoke").hexdigest(),
                    "included_paths": ["app.py"],
                },
                manifest={"manifest_hash": before_hash, "complete": True},
                plan={
                    "acceptance_criteria": [
                        {"criterion_id": "criterion-1", "required": True}
                    ],
                    "work_units": [
                        {"work_unit_id": "work-1", "expected_files": ["app.py"]}
                    ],
                },
            )
            run = control.get_project(project.project_run_id)
            plan = artifacts.get(run.current_artifact_ids["plan"])
            assert plan is not None
            control.execute(
                ProjectCommand(
                    command_type=ProjectCommandType.APPROVE_PLAN,
                    project_run_id=run.project_run_id,
                    conversation_id=run.conversation_id,
                    workspace_id=run.workspace_id,
                    repository_root=run.repository_root,
                    repository_root_fingerprint=run.repository_root_fingerprint,
                    actor_id=run.actor_id,
                    expected_state_version=run.state_version,
                    idempotency_key="phase5b-smoke-approve-plan",
                    plan_revision_id=run.current_plan_revision_id,
                    scope_revision_id=run.current_scope_revision_id,
                    manifest_hash=run.current_manifest_hash,
                    authority_scope={"operation": "prepare_work_units"},
                    artifact_id=plan.artifact_id,
                    artifact_type=plan.artifact_type.value,
                    artifact_hash=plan.content_hash,
                    artifact_binding_hash=plan.binding_hash,
                )
            )
            coordinator = ProjectCoordinatorService(database, control)
            coordinator.initialize()
            intent = coordinator.reconcile(project.project_run_id)
            if intent is None:
                raise RuntimeError("canonical coordinator did not prepare a work-unit intent")
            evidence = artifacts.put(
                build_project_artifact(
                    artifact_type=ProjectArtifactType.COORDINATOR_DECISION,
                    binding=ProjectArtifactBinding(
                        project_run_id=intent.project_run_id,
                        plan_revision_id=intent.plan_revision_id,
                        scope_revision_id=intent.scope_revision_id,
                        manifest_hash=intent.manifest_hash,
                        coordinator_intent_id=intent.coordinator_intent_id,
                    ),
                    payload={
                        "evidence": {
                            "project_run_id": intent.project_run_id,
                            "workspace_id": "phase5b-smoke-workspace",
                            "repository_root_fingerprint": before_hash,
                            "allowed_modify_paths": ["app.py"],
                            "allowed_create_paths": [],
                            "allowed_delete_paths": [],
                            "work_unit": {
                                "work_unit_id": "work-1",
                                "summary": "Propose changing VALUE from 1 to 2.",
                                "expected_files": ["app.py"],
                            },
                            "file_identities": {"app.py": before_hash},
                            "project_rag_enabled": False,
                        }
                    },
                )
            )
            invocations = ProjectModelInvocationStore(database)
            invocations.initialize()
            gateway = build_synthesis_gateway_from_environment(database_path=database)
            if isinstance(gateway, UnavailableSynthesisGateway):
                raise RuntimeError(gateway.reason)
            readiness_diagnostic = _enable_disposable_model_profile(gateway)
            orchestrator = CanonicalSynthesisOrchestrator(
                invocations=invocations,
                artifacts=artifacts,
                gateway=gateway,
                control=control,
            )
            with sqlite3.connect(database) as connection:
                authority_before = {
                    "approval_grants": _count(connection, "project_approval_grants"),
                    "worker_requests": _count(connection, "project_worker_requests"),
                    "execution_dispatches": _count(connection, "project_execution_dispatches"),
                }
            outcome = orchestrator.prepare_patch(
                intent,
                evidence,
                CanonicalProviderProfile(
                    provider=gateway.provider,
                    model_profile=gateway.model,
                    endpoint_identity=gateway.endpoint_identity,
                ),
            )
            with sqlite3.connect(database) as connection:
                authority_after = {
                    "approval_grants": _count(connection, "project_approval_grants"),
                    "worker_requests": _count(connection, "project_worker_requests"),
                    "execution_dispatches": _count(connection, "project_execution_dispatches"),
                }
            if _sha256(source) != before_hash or authority_after != authority_before:
                raise RuntimeError("advisory synthesis crossed a mutation or execution boundary")
            proposal = orchestrator.proposals.get(str(outcome.proposal_id))
            if proposal is None:
                raise RuntimeError("the immutable synthesis proposal is missing")
            diagnostic = gateway.local_ai_service.generation_diagnostic(
                proposal.generation_id
            )
            diagnostic = {**readiness_diagnostic, **diagnostic}
            print(
                json.dumps(
                    {key: diagnostic.get(key) for key in _SMOKE_DIAGNOSTIC_FIELDS},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    except Exception as exc:
        diagnostic = {
            **readiness_diagnostic,
            **_smoke_failure_diagnostic(exc, configuration),
        }
        print(
            json.dumps(
                {key: diagnostic.get(key) for key in _SMOKE_DIAGNOSTIC_FIELDS},
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
