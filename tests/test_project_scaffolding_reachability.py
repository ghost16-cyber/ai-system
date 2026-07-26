from __future__ import annotations

import hashlib
from types import SimpleNamespace

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import ProjectCommand, ProjectCommandType, ProjectControlPlane
from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_coordinator import ProjectCoordinatorExecutor, ProjectCoordinatorService
from backend.app.project_delivery.service import create_delivery_job
from backend.app.project_scaffolding import ProjectScaffoldingService, ScaffoldPersistenceService


class _SpyOrchestrator:
    """Records whether model synthesis was actually invoked."""

    def __init__(self) -> None:
        self.artifacts: ProjectArtifactStore | None = None
        self.patch_calls = 0

    def prepare_patch(self, intent, evidence_artifact, provider_profile):
        self.patch_calls += 1
        content = "SPY_SYNTHESIZED = 1\n"
        assert self.artifacts is not None
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.PATCH_PREVIEW,
            binding=ProjectArtifactBinding(
                project_run_id=intent.project_run_id,
                plan_revision_id=intent.plan_revision_id,
                scope_revision_id=intent.scope_revision_id,
                manifest_hash=intent.manifest_hash,
                coordinator_intent_id=intent.coordinator_intent_id,
                authority_hash=content_hash({"spy": "patch", "call": self.patch_calls}),
            ),
            payload={
                "patch_id": "spy-synthesized-patch",
                "work_unit_id": "wu-01",
                "operations": [{
                    "path": "spy_synthesized.py",
                    "operation": "create",
                    "new_content": content,
                    "result_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }],
                "requires_exact_approval": True,
            },
        ))
        return SimpleNamespace(
            status="prepared", artifact_id=artifact.artifact_id, artifact_hash=artifact.content_hash,
        )

    def prepare_repair(self, intent, evidence_artifact, provider_profile):
        raise AssertionError("prepare_repair should not be called by these tests")


def _command(run, kind, key, *, payload=None, authority=None, artifact=None):
    return ProjectCommand(
        command_type=kind,
        project_run_id=run.project_run_id,
        conversation_id=run.conversation_id,
        workspace_id=run.workspace_id,
        repository_root=run.repository_root,
        repository_root_fingerprint=run.repository_root_fingerprint,
        actor_id=run.actor_id,
        expected_state_version=run.state_version,
        idempotency_key=key,
        plan_revision_id=run.current_plan_revision_id,
        scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash,
        authority_scope=authority or {},
        payload=payload or {},
        artifact_id=artifact.artifact_id if artifact else None,
        artifact_type=artifact.artifact_type.value if artifact else None,
        artifact_hash=artifact.content_hash if artifact else None,
        artifact_binding_hash=artifact.binding_hash if artifact else None,
    )


def _build_canonical_project_from_chat_request(
    tmp_path, user_request: str, *, orchestrator=None, provider_profile=None,
):
    """Mirrors `main.py`'s `_start_canonical_project_from_chat` exactly:
    the same deterministic planner entry point (`create_delivery_job`) a
    real chat request goes through, feeding its output into the same
    `CanonicalProjectService.create_project(...)` call. Nothing here
    hand-constructs a work unit or a `scaffold_hint` -- both come from the
    real planner, exactly as they would for a live user request."""
    database = tmp_path / "astra.db"
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)

    built = create_delivery_job(
        root=root,
        conversation_id="conversation-1",
        folder_access_id="workspace-1",
        user_request=user_request,
        action_run_id="run-1",
        model_gateway=None,
    )
    specification = dict(built["specification"])
    plan = dict(built["plan"]) if built.get("plan") else None
    assert plan is not None, "the deterministic planner unexpectedly required clarification"
    plan["acceptance_criteria"] = list(specification.get("acceptance_criteria") or [])
    plan["configured_limits"] = dict(built.get("limits") or {})

    project = service.create_project(
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        repository_root=str(root),
        repository_root_fingerprint="root-fingerprint-1",
        actor_id="local-user",
        idempotency_key="create-project",
        folder_authority={
            "status": "completed",
            "action_id": "workspace-1",
            "conversation_id": "conversation-1",
            "workspace_id": "workspace-1",
            "repository_root_fingerprint": "root-fingerprint-1",
        },
        specification=specification,
        manifest=dict(built["project_state_manifest"]),
        plan=plan,
    )
    run = control.get_project(project.project_run_id)
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "approve-plan",
        authority={
            "operation": "prepare_work_units",
            "work_unit_ids": [str(unit["work_unit_id"]) for unit in plan["work_units"]],
        },
        artifact=artifacts.get(run.current_artifact_ids["plan"]),
    ))
    coordinator = ProjectCoordinatorService(database, control)
    coordinator.initialize()
    coordinator.reconcile(project.project_run_id)

    scaffolding = ProjectScaffoldingService()
    scaffold_persistence = ScaffoldPersistenceService(database, control, artifacts)
    scaffold_persistence.initialize()
    executor = ProjectCoordinatorExecutor(
        coordinator,
        control,
        artifacts,
        scaffolding=scaffolding,
        scaffold_persistence=scaffold_persistence,
        orchestrator=orchestrator,
        provider_profile=provider_profile,
    )
    return control, artifacts, coordinator, executor, project.project_run_id, plan, root


def test_supported_instruction_reaches_scaffolding_through_the_real_planner_without_calling_synthesis(
    tmp_path,
) -> None:
    spy = _SpyOrchestrator()
    control, artifacts, coordinator, executor, project_id, plan, root = (
        _build_canonical_project_from_chat_request(
            tmp_path,
            "Create billing/__init__.py and billing/module.py for a new billing package.",
            orchestrator=spy,
            provider_profile=object(),
        )
    )
    spy.artifacts = artifacts

    # The real deterministic planner -- not this test -- must have produced
    # the hint.
    work_unit = plan["work_units"][0]
    assert work_unit["scaffold_hint"] == {
        "category": "python_package",
        "inputs": {"package_name": "billing"},
    }

    assert executor.run_once("coordinator-worker") is True
    assert spy.patch_calls == 0

    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    preview = artifacts.get(run.current_artifact_ids["patch_preview"])
    assert preview.payload["scaffold_blueprint_id"] == "python_package"
    manifest = artifacts.get(preview.payload["scaffold_manifest_artifact_id"])
    assert manifest is not None
    assert manifest.artifact_type == ProjectArtifactType.SCAFFOLD_MANIFEST
    operations_by_path = {op["path"]: op for op in preview.payload["operations"]}
    assert set(operations_by_path) == {"billing/__init__.py", "billing/module.py"}

    patch_id = str(run.pending_user_action).split(":", 1)[1]
    work_unit_id = str(work_unit["work_unit_id"])
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PATCH,
        "approve-patch",
        payload={"patch_id": patch_id, "work_unit_id": work_unit_id},
        authority={"patch_id": patch_id, "work_unit_id": work_unit_id, "operation": "apply_exact_patch"},
        artifact=preview,
    ))
    run = control.get_project(project_id)
    control.execute(_command(
        run,
        ProjectCommandType.BEGIN_PATCH_APPLICATION,
        "begin-patch",
        payload={"patch_id": patch_id},
        authority={"patch_id": patch_id},
    ))
    run = control.get_project(project_id)

    for operation in preview.payload["operations"]:
        target = root / operation["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(operation["new_content"], encoding="utf-8")

    patch_attempt = next(
        item for item in control.list_attempts(project_id)
        if item.attempt_type == ExecutionAttemptType.PATCH
    )
    result_artifact = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
        binding=ProjectArtifactBinding(
            project_run_id=project_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            execution_attempt_id=patch_attempt.execution_attempt_id,
            authority_hash=content_hash({"patch_id": patch_id}),
        ),
        payload={"patch_id": patch_id, "succeeded": True},
    ))
    resulting_manifest = content_hash({
        op["path"]: op["new_content"] for op in preview.payload["operations"]
    })
    control.execute(_command(
        run,
        ProjectCommandType.RECORD_PATCH_RESULT,
        "record-patch",
        payload={
            "patch_id": patch_id,
            "execution_attempt_id": patch_attempt.execution_attempt_id,
            "succeeded": True,
            "resulting_manifest_hash": resulting_manifest,
            "result_reference": {"artifact_id": result_artifact.artifact_id},
        },
        authority={"patch_id": patch_id},
        artifact=result_artifact,
    ))
    coordinator.reconcile(project_id)

    reached_handoff_ready = False
    run = control.get_project(project_id)
    for _ in range(10):
        progressed = executor.run_once("coordinator-worker")
        run = control.get_project(project_id)
        if run.pending_user_action in {"request_handoff", "finalize_project"}:
            reached_handoff_ready = True
            break
        if not progressed:
            break
    assert reached_handoff_ready, (
        f"did not reach handoff readiness; lifecycle={run.lifecycle_status.value!r} "
        f"pending_user_action={run.pending_user_action!r}"
    )
    assert spy.patch_calls == 0, "model synthesis must never be invoked for a scaffolded work unit"


def test_unsupported_instruction_still_falls_back_to_synthesis_through_the_real_planner(tmp_path) -> None:
    spy = _SpyOrchestrator()
    control, artifacts, coordinator, executor, project_id, plan, root = (
        _build_canonical_project_from_chat_request(
            tmp_path,
            "Fix the calculation bug in src/app.py.",
            orchestrator=spy,
            provider_profile=object(),
        )
    )
    spy.artifacts = artifacts

    work_unit = plan["work_units"][0]
    assert work_unit.get("scaffold_hint") is None

    assert executor.run_once("coordinator-worker") is True

    assert spy.patch_calls == 1
    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    preview = artifacts.get(run.current_artifact_ids["patch_preview"])
    assert preview.payload["patch_id"] == "spy-synthesized-patch"
