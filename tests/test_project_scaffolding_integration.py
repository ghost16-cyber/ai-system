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
from backend.app.project_coordinator import (
    CoordinatorIntentStatus,
    ProjectCoordinatorExecutor,
    ProjectCoordinatorService,
)
from backend.app.project_scaffolding import ProjectScaffoldingService, ScaffoldPersistenceService
from backend.app.project_scaffolding.validators import (
    ConflictingDestinationError,
    DestinationPathError,
    DuplicateTemplateReferenceError,
    RenderIntegrityError,
    UnresolvedPlaceholderError,
)


class _SpyOrchestrator:
    """A minimal synthesis-orchestrator double: records whether/how often it
    was actually invoked, so tests can prove synthesis was (or was not)
    reached, rather than only observing the run's final blocked/unblocked
    state."""

    def __init__(self, artifacts: ProjectArtifactStore) -> None:
        self.artifacts = artifacts
        self.patch_calls = 0

    def prepare_patch(self, intent, evidence_artifact, provider_profile):
        self.patch_calls += 1
        content = "SPY_SYNTHESIZED = 1\n"
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
                "work_unit_id": "work-1",
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


class _RaisingScaffolding:
    """A scaffolding-service double whose `render()` always raises a fixed
    exception -- used to prove the coordinator's exception classification
    (not-applicable vs. fail-closed) for failure modes that a real,
    well-formed registered blueprint cannot organically reach (e.g. a
    render-integrity mismatch, which the real renderer/validator pipeline
    can never actually produce since it computes and checks its own hashes
    in the same call)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def render(self, *, category, inputs, repository_root=None, requested_version=None, detected=None):
        raise self._exc


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


def _runtime(
    tmp_path,
    *,
    scaffold_hint=None,
    wire_scaffolding=True,
    scaffolding_override=None,
    orchestrator=None,
    provider_profile=None,
):
    database = tmp_path / "astra.db"
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)

    work_unit = {
        "work_unit_id": "work-1",
        "expected_files": ["billing/__init__.py", "billing/module.py"],
        "acceptance_criteria_ids": ["criterion-1"],
        "patch_operations": [],
    }
    if scaffold_hint is not None:
        work_unit["scaffold_hint"] = scaffold_hint

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
        specification={
            "specification_id": "spec-1",
            "specification_hash": "1" * 64,
            "revision": 1,
            "included_paths": ["billing/__init__.py", "billing/module.py"],
            "allowed_operations": ["read", "approved_patch", "verification"],
        },
        manifest={
            "manifest_hash": "2" * 64,
            "complete": True,
            "revision": 1,
            "entries": [],
        },
        plan={
            "revision": 1,
            "acceptance_criteria": [{
                "criterion_id": "criterion-1",
                "required": True,
                "verification_mode": "structural_code_inspection",
            }],
            "work_units": [work_unit],
            "configured_limits": {"max_work_units": 2, "max_verifications": 2},
        },
    )
    run = control.get_project(project.project_run_id)
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "approve-plan",
        authority={"operation": "prepare_work_units", "work_unit_ids": ["work-1"]},
        artifact=artifacts.get(run.current_artifact_ids["plan"]),
    ))
    coordinator = ProjectCoordinatorService(database, control)
    coordinator.initialize()
    coordinator.reconcile(project.project_run_id)

    scaffolding = None
    scaffold_persistence = None
    if wire_scaffolding:
        scaffolding = scaffolding_override if scaffolding_override is not None else ProjectScaffoldingService()
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
    return control, artifacts, coordinator, executor, project.project_run_id


def test_scaffold_hint_produces_a_patch_preview_instead_of_blocking(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {"package_name": "billing"}},
    )

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    previews = artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW)
    assert len(previews) == 1
    preview = previews[0]
    operations_by_path = {op["path"]: op for op in preview.payload["operations"]}
    assert set(operations_by_path) == {"billing/__init__.py", "billing/module.py"}
    assert preview.payload["scaffold_blueprint_id"] == "python_package"
    manifest_artifact_id = preview.payload["scaffold_manifest_artifact_id"]
    manifest_artifact = artifacts.get(manifest_artifact_id)
    assert manifest_artifact is not None
    assert manifest_artifact.artifact_type == ProjectArtifactType.SCAFFOLD_MANIFEST
    assert manifest_artifact.binding.project_run_id == project_id
    assert manifest_artifact.binding.work_unit_id == "work-1"
    assert any(
        ref.get("artifact_id") == manifest_artifact_id for ref in preview.evidence_references
    )


def test_scaffolded_patch_flows_through_the_existing_approval_and_handoff_chain(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {"package_name": "billing"}},
    )
    executor.run_once("coordinator-worker")
    run = control.get_project(project_id)
    patch_id = str(run.pending_user_action).split(":", 1)[1]

    preview = artifacts.get(run.current_artifact_ids["patch_preview"])
    for operation in preview.payload["operations"]:
        target = tmp_path / "workspace" / operation["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(operation["new_content"], encoding="utf-8")

    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PATCH,
        "approve-patch",
        payload={"patch_id": patch_id, "work_unit_id": "work-1"},
        authority={"patch_id": patch_id, "work_unit_id": "work-1", "operation": "apply_exact_patch"},
        artifact=artifacts.get(run.current_artifact_ids["patch_preview"]),
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
    patch_attempt = next(
        item for item in control.list_attempts(project_id)
        if item.attempt_type == ExecutionAttemptType.PATCH
    )
    from backend.app.project_artifacts import ProjectArtifactBinding, build_project_artifact

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
    resulting_manifest = content_hash({"billing/__init__.py": "", "billing/module.py": ""})
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

    assert executor.run_once("coordinator-worker") is True
    run = control.get_project(project_id)
    assert run.work_unit_state["work-1"]["status"] == "completed"
    assert run.pending_user_action == "request_handoff"

    assert executor.run_once("coordinator-worker") is True
    run = control.get_project(project_id)
    assert run.pending_user_action == "finalize_project"


def test_no_scaffold_hint_falls_through_to_existing_block_behavior_unchanged(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(tmp_path, scaffold_hint=None)

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.lifecycle_status.value == "blocked"
    assert run.pending_user_action == "revise_plan"
    assert artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW) == []


def test_scaffold_hint_with_unregistered_category_falls_through_to_block_not_a_crash(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path, scaffold_hint={"category": "does_not_exist", "inputs": {}},
    )

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.lifecycle_status.value == "blocked"
    assert run.pending_user_action == "revise_plan"


def test_scaffold_hint_without_scaffolding_wired_in_behaves_exactly_as_before(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {"package_name": "billing"}},
        wire_scaffolding=False,
    )

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.lifecycle_status.value == "blocked"
    assert run.pending_user_action == "revise_plan"
    assert artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW) == []


# --- Fallback-classification regression tests -------------------------------
#
# "Not applicable" (no hint, or no registered blueprint matches the hinted
# category) must still fall back to model synthesis -- proven below by a spy
# orchestrator that really gets invoked. A "supported category, but the
# deterministic attempt failed validation/security/integrity checks" must
# instead fail closed via the existing CoordinatorPolicyBlock path, and must
# never reach the spy orchestrator at all.


def test_no_scaffold_hint_falls_back_to_synthesis_which_is_actually_invoked(tmp_path) -> None:
    spy = _SpyOrchestrator(None)
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path, scaffold_hint=None, orchestrator=spy, provider_profile=object(),
    )
    spy.artifacts = artifacts

    assert executor.run_once("coordinator-worker") is True

    assert spy.patch_calls == 1
    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    preview = artifacts.get(run.current_artifact_ids["patch_preview"])
    assert preview.payload["patch_id"] == "spy-synthesized-patch"


def test_unsupported_category_falls_back_to_synthesis_which_is_actually_invoked(tmp_path) -> None:
    spy = _SpyOrchestrator(None)
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path,
        scaffold_hint={"category": "does_not_exist", "inputs": {}},
        orchestrator=spy,
        provider_profile=object(),
    )
    spy.artifacts = artifacts

    assert executor.run_once("coordinator-worker") is True

    assert spy.patch_calls == 1
    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    preview = artifacts.get(run.current_artifact_ids["patch_preview"])
    assert preview.payload["patch_id"] == "spy-synthesized-patch"
    assert artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST) == []


def _assert_fails_closed_without_reaching_synthesis(tmp_path, *, scaffold_hint, scaffolding_override) -> None:
    spy = _SpyOrchestrator(None)
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path,
        scaffold_hint=scaffold_hint,
        scaffolding_override=scaffolding_override,
        orchestrator=spy,
        provider_profile=object(),
    )
    spy.artifacts = artifacts

    assert executor.run_once("coordinator-worker") is True

    assert spy.patch_calls == 0, "a fail-closed scaffold failure must never reach model synthesis"
    run = control.get_project(project_id)
    assert run.lifecycle_status.value == "blocked"
    assert run.pending_user_action == "revise_plan"
    assert artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW) == []
    assert artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST) == []
    decisions = artifacts.list_for_project(project_id, artifact_type=ProjectArtifactType.COORDINATOR_DECISION)
    assert decisions
    assert "failed validation" in decisions[0].payload["reason"]


def test_missing_required_input_on_a_supported_blueprint_fails_closed(tmp_path) -> None:
    # Real python_package blueprint, no scaffolding double -- proves this is
    # organically reachable, not just a classification-logic exercise.
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {}},
        scaffolding_override=None,
    )


def test_invalid_required_input_value_on_a_supported_blueprint_fails_closed(tmp_path) -> None:
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {"package_name": "NOT VALID"}},
        scaffolding_override=None,
    )


def test_path_traversal_fails_closed_and_does_not_call_synthesis(tmp_path) -> None:
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {}},
        scaffolding_override=_RaisingScaffolding(DestinationPathError("destination escapes the repository root")),
    )


def test_conflicting_destination_fails_closed_and_does_not_call_synthesis(tmp_path) -> None:
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {}},
        scaffolding_override=_RaisingScaffolding(ConflictingDestinationError("two files target the same destination")),
    )


def test_duplicate_template_reference_fails_closed_and_does_not_call_synthesis(tmp_path) -> None:
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {}},
        scaffolding_override=_RaisingScaffolding(DuplicateTemplateReferenceError("template reused across files")),
    )


def test_unresolved_placeholder_fails_closed_and_does_not_call_synthesis(tmp_path) -> None:
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {}},
        scaffolding_override=_RaisingScaffolding(UnresolvedPlaceholderError("unresolved template placeholder")),
    )


def test_render_integrity_failure_fails_closed_and_does_not_call_synthesis(tmp_path) -> None:
    # The real renderer/validator pipeline computes and checks its own
    # hashes in the same call, so a genuine hash mismatch can never
    # organically occur through ProjectScaffoldingService.render() --
    # this proves the coordinator's classification of the exception type
    # itself, exactly as validate_render_integrity's own unit tests already
    # prove the check itself is correct (test_project_scaffolding_validators.py).
    _assert_fails_closed_without_reaching_synthesis(
        tmp_path,
        scaffold_hint={"category": "python_package", "inputs": {}},
        scaffolding_override=_RaisingScaffolding(RenderIntegrityError("declared hash does not match content")),
    )
