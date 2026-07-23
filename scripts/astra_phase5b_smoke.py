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
from backend.app.project_analysis.model_synthesis import (
    CanonicalProviderProfile,
    CanonicalSynthesisOrchestrator,
    UnavailableSynthesisGateway,
    build_synthesis_gateway_from_environment,
)
from backend.app.project_analysis.model_synthesis.gateway import SynthesisGatewayError
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
            diagnostic = gateway.local_gateway.safe_generation_diagnostic(
                proposal.generation_id
            )
            allowed = (
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
            )
            print(
                json.dumps(
                    {key: diagnostic.get(key) for key in allowed},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    except Exception as exc:
        current: BaseException | None = exc
        diagnostic: dict[str, object] = {}
        while current is not None:
            if isinstance(current, SynthesisGatewayError):
                diagnostic = dict(current.diagnostic)
                diagnostic.setdefault("generation_failure_classification", current.code)
                break
            current = current.__cause__
        if not diagnostic:
            diagnostic = {
                "generation_failure_classification": "smoke_internal_failure",
                "validation_error_location": None,
                "validation_error_type": None,
                "provider_identity": configuration.provider_type,
                "exact_model_tag": configuration.synthesis_model,
                "response_schema_identity": "astra.project-synthesis.response.v1",
                "response_schema_hash": None,
                "duration_ms": None,
                "prompt_eval_count": None,
                "eval_count": None,
            }
        allowed = (
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
        )
        print(
            json.dumps({key: diagnostic.get(key) for key in allowed}, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
