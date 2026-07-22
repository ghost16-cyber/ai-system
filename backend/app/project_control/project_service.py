from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.project_artifacts import (
    ProjectArtifact,
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control.contracts import (
    ProjectCommand,
    ProjectCommandType,
    ProjectReadModel,
    ProjectRun,
    content_hash,
)
from backend.app.project_control.errors import ProjectControlError, ProjectControlErrorCode
from backend.app.project_control.compatibility import CompatibilityClassificationService
from backend.app.project_control.service import ProjectControlPlane


class CanonicalProjectService:
    """Canonical project creation and immutable artifact orchestration.

    This service prepares commands and artifacts. ProjectControlPlane remains the
    only lifecycle writer, and ProjectArtifactStore remains the only artifact
    writer.
    """

    def __init__(
        self,
        control: ProjectControlPlane,
        artifact_store: ProjectArtifactStore,
    ) -> None:
        self.control = control
        self.artifact_store = artifact_store

    def create_project(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        repository_root: str | Path,
        repository_root_fingerprint: str,
        actor_id: str,
        idempotency_key: str,
        folder_authority: dict[str, Any],
        specification: dict[str, Any],
        manifest: dict[str, Any],
        plan: dict[str, Any] | None,
        project_run_id: str | None = None,
    ) -> ProjectReadModel:
        run_id = self.initialize_project(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            repository_root=repository_root,
            repository_root_fingerprint=repository_root_fingerprint,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            folder_authority=folder_authority,
            project_run_id=project_run_id,
        )
        self.record_specification(
            project_run_id=run_id,
            specification=specification,
            idempotency_key=f"{idempotency_key}:specification",
            expected_state_version=1,
        )
        self.record_manifest(
            project_run_id=run_id,
            manifest=manifest,
            idempotency_key=f"{idempotency_key}:manifest",
            expected_state_version=2,
        )
        if bool(manifest.get("complete")) and plan is not None:
            self.record_plan(
                project_run_id=run_id,
                plan=plan,
                idempotency_key=f"{idempotency_key}:plan",
                expected_state_version=3,
            )
        elif specification.get("clarification_questions"):
            run = self.control.get_project(run_id)
            self.control.execute(
                ProjectCommand(
                    command_type=ProjectCommandType.REQUEST_CLARIFICATION,
                    expected_state_version=3,
                    idempotency_key=f"{idempotency_key}:clarification",
                    plan_revision_id=run.current_plan_revision_id,
                    scope_revision_id=run.current_scope_revision_id,
                    manifest_hash=run.current_manifest_hash,
                    payload={
                        "reason": str(specification["clarification_questions"][0])
                    },
                    **self._base(
                        project_run_id=run.project_run_id,
                        conversation_id=run.conversation_id,
                        workspace_id=run.workspace_id,
                        repository_root=run.repository_root,
                        repository_root_fingerprint=run.repository_root_fingerprint,
                        actor_id=run.actor_id,
                    ),
                )
            )
        return self.control.get_read_model(run_id)

    def import_historical_record(
        self,
        *,
        historical_job: dict[str, Any],
        conversation_id: str,
        workspace_id: str,
        repository_root: str | Path,
        repository_root_fingerprint: str,
        folder_authority: dict[str, Any],
        idempotency_key: str,
        actor_id: str = "local-user",
    ) -> ProjectReadModel:
        """Explicitly re-import a historical record as a fresh canonical run.

        This is the only supported path from a read-only historical record to a
        mutable canonical run. It never happens implicitly from a legacy approval
        route. The new run is awaiting its normal approvals; the historical
        record and its identity are left untouched.
        """
        legacy_id = str(historical_job.get("delivery_job_id") or "")
        if not legacy_id:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                "The historical record has no durable identity to import.",
            )
        if not CompatibilityClassificationService(
            self.control.database_path
        ).is_historical_read_only(legacy_id):
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Only a ledger-classified historical record can be explicitly imported.",
            )
        specification = dict(historical_job.get("specification") or {})
        manifest = dict(historical_job.get("project_state_manifest") or {})
        plan = dict(
            historical_job.get("plan_revision") or historical_job.get("plan") or {}
        )
        spec_hash = str(
            specification.get("specification_hash")
            or content_hash({"import_specification": legacy_id})
        )
        specification = {**specification, "specification_hash": spec_hash}
        manifest_hash = str(
            manifest.get("manifest_hash")
            or historical_job.get("project_state_hash")
            or content_hash({"import_manifest": legacy_id})
        )
        manifest = {
            **manifest,
            "manifest_hash": manifest_hash,
            "complete": bool(manifest.get("complete")),
        }
        return self.create_project(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            repository_root=repository_root,
            repository_root_fingerprint=repository_root_fingerprint,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            folder_authority=folder_authority,
            specification=specification,
            manifest=manifest,
            plan=plan or None,
        )

    def initialize_project(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        repository_root: str | Path,
        repository_root_fingerprint: str,
        actor_id: str,
        idempotency_key: str,
        folder_authority: dict[str, Any],
        project_run_id: str | None = None,
    ) -> str:
        """Durably issue canonical identity before analysis or model work begins."""
        self._validate_folder_authority(
            folder_authority,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            repository_root=str(Path(repository_root).resolve()),
            repository_root_fingerprint=repository_root_fingerprint,
        )
        root = str(Path(repository_root).resolve())
        run_id = project_run_id or (
            f"project-{content_hash([conversation_id, workspace_id, idempotency_key])[:24]}"
        )
        self.control.execute(
            ProjectCommand(
                command_type=ProjectCommandType.INITIALIZE_PROJECT,
                expected_state_version=0,
                idempotency_key=f"{idempotency_key}:initialize",
                payload={"canonical_generation": "canonical"},
                **self._base(
                    project_run_id=run_id,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    repository_root=root,
                    repository_root_fingerprint=repository_root_fingerprint,
                    actor_id=actor_id,
                ),
            )
        )
        return run_id

    def record_specification(
        self,
        *,
        project_run_id: str,
        specification: dict[str, Any],
        idempotency_key: str,
        expected_state_version: int | None = None,
    ) -> ProjectReadModel:
        run = self.control.get_project(project_run_id)
        replay = self._existing_artifact_replay(
            run,
            idempotency_key,
            ProjectArtifactType.SPECIFICATION,
            specification,
            int(specification.get("revision") or 1),
        )
        if replay is not None:
            return replay
        specification_hash = str(
            specification.get("specification_hash") or content_hash(specification)
        )
        artifact = self._put_artifact(
            run,
            ProjectArtifactType.SPECIFICATION,
            specification,
            revision_number=int(specification.get("revision") or 1),
        )
        payload = {
            "task_specification_id": str(
                specification.get("specification_id")
                or f"specification-{specification_hash[:24]}"
            ),
            "specification_hash": specification_hash,
            "included_paths": list(specification.get("included_paths") or ()),
            "excluded_paths": list(
                specification.get("excluded_paths")
                or specification.get("explicit_exclusions")
                or ()
            ),
            "allowed_operations": list(
                specification.get("allowed_operations")
                or ("read", "patch_preview", "approved_patch", "approved_command", "verification")
            ),
            "reason": str(specification.get("reason") or "Canonical project specification."),
        }
        self.control.execute(
            self._artifact_command(
                run,
                ProjectCommandType.ATTACH_SPECIFICATION,
                artifact,
                payload,
                idempotency_key,
                expected_state_version=expected_state_version,
            )
        )
        return self.control.get_read_model(project_run_id)

    def record_manifest(
        self,
        *,
        project_run_id: str,
        manifest: dict[str, Any],
        idempotency_key: str,
        expected_state_version: int | None = None,
    ) -> ProjectReadModel:
        run = self.control.get_project(project_run_id)
        replay = self._existing_artifact_replay(
            run,
            idempotency_key,
            ProjectArtifactType.MANIFEST,
            manifest,
            int(manifest.get("revision") or 1),
        )
        if replay is not None:
            return replay
        manifest_hash = str(manifest.get("manifest_hash") or content_hash(manifest))
        artifact = self._put_artifact(
            run,
            ProjectArtifactType.MANIFEST,
            manifest,
            revision_number=int(manifest.get("revision") or 1),
            manifest_hash=manifest_hash,
        )
        payload = {
            "manifest_hash": manifest_hash,
            "complete": bool(manifest.get("complete")),
            "incomplete_reasons": list(manifest.get("incomplete_reasons") or ()),
        }
        self.control.execute(
            self._artifact_command(
                run,
                ProjectCommandType.REGISTER_MANIFEST,
                artifact,
                payload,
                idempotency_key,
                expected_state_version=expected_state_version,
            )
        )
        return self.control.get_read_model(project_run_id)

    def record_plan(
        self,
        *,
        project_run_id: str,
        plan: dict[str, Any],
        idempotency_key: str,
        expected_state_version: int | None = None,
    ) -> ProjectReadModel:
        run = self.control.get_project(project_run_id)
        replay = self._existing_artifact_replay(
            run,
            idempotency_key,
            ProjectArtifactType.PLAN,
            plan,
            int(plan.get("revision") or 1),
        )
        if replay is not None:
            return replay
        artifact = self._put_artifact(
            run,
            ProjectArtifactType.PLAN,
            plan,
            revision_number=int(plan.get("revision") or 1),
        )
        payload = {
            "acceptance_criteria": list(plan.get("acceptance_criteria") or ()),
            "work_units": list(plan.get("work_units") or ()),
            "configured_limits": dict(plan.get("configured_limits") or plan.get("limits") or {}),
            "reason": str(plan.get("reason") or "Canonical immutable execution plan."),
        }
        self.control.execute(
            self._artifact_command(
                run,
                ProjectCommandType.PROPOSE_PLAN_REVISION,
                artifact,
                payload,
                idempotency_key,
                expected_state_version=expected_state_version,
            )
        )
        return self.control.get_read_model(project_run_id)

    def revise_scope(
        self,
        *,
        project_run_id: str,
        specification: dict[str, Any],
        idempotency_key: str,
        reason: str,
    ) -> ProjectReadModel:
        run = self.control.get_project(project_run_id)
        revision = len(
            self.artifact_store.list_for_project(
                project_run_id, artifact_type=ProjectArtifactType.SPECIFICATION
            )
        ) + 1
        specification_hash = str(
            specification.get("specification_hash") or content_hash(specification)
        )
        artifact = self._put_artifact(
            run,
            ProjectArtifactType.SPECIFICATION,
            specification,
            revision_number=revision,
        )
        payload = {
            "specification_hash": specification_hash,
            "included_paths": list(specification.get("included_paths") or ()),
            "excluded_paths": list(specification.get("excluded_paths") or ()),
            "allowed_operations": list(specification.get("allowed_operations") or ()),
            "reason": reason,
        }
        self.control.execute(
            self._artifact_command(
                run,
                ProjectCommandType.REVISE_SCOPE,
                artifact,
                payload,
                idempotency_key,
            )
        )
        return self.control.get_read_model(project_run_id)

    def get_project(self, project_run_id: str) -> ProjectReadModel:
        return self.control.get_read_model(project_run_id)

    def execute_action(
        self,
        project_run_id: str,
        *,
        action: str,
        request: Any,
    ) -> ProjectReadModel:
        """Submit one exactly bound user action to the lifecycle authority."""
        run = self.control.get_project(project_run_id)
        identity = {
            "conversation_id": request.conversation_id,
            "workspace_id": request.workspace_id,
            "actor_id": request.actor_id,
            "repository_root_fingerprint": request.repository_root_fingerprint,
        }
        expected_identity = {
            "conversation_id": run.conversation_id,
            "workspace_id": run.workspace_id,
            "actor_id": run.actor_id,
            "repository_root_fingerprint": run.repository_root_fingerprint,
        }
        if identity != expected_identity:
            raise ProjectControlError(
                ProjectControlErrorCode.WORKSPACE_MISMATCH,
                "The canonical action identity does not match this project.",
            )
        pending = str(run.pending_user_action or "")
        command_types = {
            "approve_plan": ProjectCommandType.APPROVE_PLAN,
            "approve_patch": ProjectCommandType.APPROVE_PATCH,
            "approve_command": ProjectCommandType.APPROVE_COMMAND,
            "approve_rollback": ProjectCommandType.APPROVE_ROLLBACK,
            "approve_manual_verification": ProjectCommandType.APPROVE_COMMAND,
            "cancel_project": ProjectCommandType.CANCEL_PROJECT,
        }
        command_type = command_types.get(action)
        if command_type is None:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "This canonical action is not supported.")
        replay_payload = dict(request.payload)
        replay_authority: dict[str, Any] = {"action": action}
        if action != "cancel_project":
            replay_authority.update({
                "artifact_id": request.artifact_id,
                "artifact_type": request.artifact_type,
                "artifact_hash": request.artifact_hash,
                "artifact_binding_hash": request.artifact_binding_hash,
            })
        object_key = {
            "approve_patch": "patch_id",
            "approve_command": "command_id",
            "approve_rollback": "rollback_id",
        }.get(action)
        if object_key and replay_payload.get(object_key):
            replay_authority[object_key] = replay_payload[object_key]
        if action == "approve_plan":
            replay_authority.update({
                "operation": "prepare_work_units",
                "plan_artifact_id": request.artifact_id,
                "plan_artifact_hash": request.artifact_hash,
            })
        elif action == "approve_patch":
            replay_authority["operation"] = "apply_exact_patch"
        elif action == "approve_command":
            replay_authority["operation"] = "execute_exact_command"
            if replay_payload.get("execution_hash"):
                replay_authority["execution_hash"] = replay_payload["execution_hash"]
        elif action == "approve_rollback":
            replay_authority["operation"] = "apply_exact_rollback"
        elif action == "approve_manual_verification":
            replay_payload["approval_type"] = "manual_verification"
            replay_authority["operation"] = "accept_manual_verification"
        else:
            replay_payload.setdefault("reason", "Cancelled through the canonical project API.")
        # Do NOT gate the replay lookup on live current-artifact state here: an
        # already-completed action's artifact may have been legitimately
        # superseded by later work since it committed (e.g. a scope revision).
        # `replay_completed` must return the durable stored result for an exact
        # resend without evaluating live state; only a genuinely new command
        # (replay is None, below) is checked against the current artifact.
        replay = self.control.replay_completed(ProjectCommand(
            command_type=command_type,
            project_run_id=run.project_run_id,
            conversation_id=request.conversation_id,
            workspace_id=request.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=request.repository_root_fingerprint,
            actor_id=request.actor_id,
            expected_state_version=request.expected_state_version,
            idempotency_key=request.idempotency_key,
            plan_revision_id=request.plan_revision_id,
            scope_revision_id=request.scope_revision_id,
            manifest_hash=request.manifest_hash,
            artifact_id=request.artifact_id if action != "cancel_project" else None,
            artifact_type=request.artifact_type if action != "cancel_project" else None,
            artifact_hash=request.artifact_hash if action != "cancel_project" else None,
            artifact_binding_hash=request.artifact_binding_hash if action != "cancel_project" else None,
            authority_scope=replay_authority,
            payload=replay_payload,
        ))
        if replay is not None:
            return ProjectReadModel.model_validate(replay.read_model)
        if action != "cancel_project" and pending.split(":", 1)[0] != action:
            raise ProjectControlError(ProjectControlErrorCode.ILLEGAL_TRANSITION, "This action is not currently permitted.")
        if action == "cancel_project" and run.lifecycle_status.value in {"cancelled", "completed"}:
            raise ProjectControlError(ProjectControlErrorCode.TERMINAL_PROJECT, "A terminal project cannot be cancelled again.")
        required_artifact_types = {
            "approve_plan": ("plan",),
            "approve_patch": ("patch_preview", "repair_preview"),
            "approve_command": ("command_preview",),
            "approve_rollback": ("rollback_preview",),
            "approve_manual_verification": ("verifier_result",),
        }
        if action != "cancel_project":
            allowed_types = required_artifact_types.get(action, ())
            if request.artifact_type not in allowed_types:
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "The approved artifact type is not valid for this action.",
                )
            current_artifact_id = run.current_artifact_ids.get(str(request.artifact_type))
            if current_artifact_id is None or current_artifact_id != request.artifact_id:
                raise ProjectControlError(
                    ProjectControlErrorCode.NON_CURRENT_ARTIFACT,
                    "The approved artifact is not the current artifact for this action.",
                )
            artifact = next((
                item for item in self.artifact_store.list_for_project(project_run_id)
                if item.artifact_id == request.artifact_id
            ), None)
            if artifact is None or (
                artifact.artifact_type.value != request.artifact_type
                or artifact.content_hash != request.artifact_hash
                or artifact.binding_hash != request.artifact_binding_hash
            ):
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "The approved artifact identity, type, content hash, or binding hash is not current.",
                )
        payload = dict(request.payload)
        authority: dict[str, Any] = {"action": action}
        if action != "cancel_project":
            authority.update({
                "artifact_id": request.artifact_id,
                "artifact_type": request.artifact_type,
                "artifact_hash": request.artifact_hash,
                "artifact_binding_hash": request.artifact_binding_hash,
            })
        if ":" in pending and action != "cancel_project":
            object_id = pending.split(":", 1)[1]
            key = {
                "approve_patch": "patch_id",
                "approve_command": "command_id",
                "approve_rollback": "rollback_id",
            }.get(action)
            if key:
                if payload.get(key) not in (None, object_id):
                    raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "The action payload does not match the pending object.")
                payload[key] = object_id
                authority[key] = object_id
        if action == "approve_plan":
            authority.update({
                "operation": "prepare_work_units",
                "plan_artifact_id": request.artifact_id,
                "plan_artifact_hash": request.artifact_hash,
            })
        elif action == "approve_patch":
            authority["operation"] = "apply_exact_patch"
        elif action == "approve_command":
            authority["operation"] = "execute_exact_command"
            if payload.get("execution_hash"):
                authority["execution_hash"] = payload["execution_hash"]
        elif action == "approve_rollback":
            authority["operation"] = "apply_exact_rollback"
        elif action == "approve_manual_verification":
            payload["approval_type"] = "manual_verification"
            authority["operation"] = "accept_manual_verification"
        else:
            payload.setdefault("reason", "Cancelled through the canonical project API.")
        self.control.execute(ProjectCommand(
            command_type=command_type,
            project_run_id=run.project_run_id,
            conversation_id=request.conversation_id,
            workspace_id=request.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=request.repository_root_fingerprint,
            actor_id=request.actor_id,
            expected_state_version=request.expected_state_version,
            idempotency_key=request.idempotency_key,
            plan_revision_id=request.plan_revision_id,
            scope_revision_id=request.scope_revision_id,
            manifest_hash=request.manifest_hash,
            artifact_id=request.artifact_id if action != "cancel_project" else None,
            artifact_type=request.artifact_type if action != "cancel_project" else None,
            artifact_hash=request.artifact_hash if action != "cancel_project" else None,
            artifact_binding_hash=request.artifact_binding_hash if action != "cancel_project" else None,
            authority_scope=authority,
            payload=payload,
        ))
        return self.control.get_read_model(project_run_id)

    def list_projects(self, conversation_id: str) -> list[ProjectReadModel]:
        return self.control.list_projects_for_conversation(conversation_id)

    def submit_manual_evidence(self, project_run_id: str, request: Any) -> ProjectReadModel:
        run = self.control.get_project(project_run_id)
        read_model = self.control.get_read_model(project_run_id)
        active_attempt_id = read_model.active_execution_attempt_id
        active_work_unit_id = read_model.current_work_unit
        expected_authority_binding = {
            "operation": "submit_manual_evidence",
            "project_run_id": project_run_id,
            "criterion_id": request.criterion_id,
            "work_unit_id": request.work_unit_id,
            "execution_attempt_id": request.execution_attempt_id,
        }
        if request.authority_binding != expected_authority_binding:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Manual evidence authority binding is incomplete or forged.",
            )
        replay_authority = {
            "operation": "submit_manual_evidence", "criterion_id": request.criterion_id,
            "criterion_hash": request.criterion_hash, "work_unit_id": request.work_unit_id,
            "execution_attempt_id": request.execution_attempt_id,
            "verification_artifact_id": request.verification_artifact_id,
            "verification_artifact_hash": request.verification_artifact_hash,
            "authority_binding": request.authority_binding,
        }
        replay_evidence = {
            "evidence_kind": request.evidence_kind, "evidence": request.evidence,
            "comments": request.comments, "reviewer_id": request.actor_id,
        }
        replay_evidence_hash = content_hash(replay_evidence)
        replay_evidence_id = f"manual-{content_hash([project_run_id, request.idempotency_key, replay_evidence_hash])[:24]}"
        replay_artifact = self.artifact_store.put(build_project_artifact(
            artifact_type=ProjectArtifactType.MANUAL_EVIDENCE,
            binding=ProjectArtifactBinding(
                project_run_id=project_run_id, plan_revision_id=request.plan_revision_id,
                scope_revision_id=request.scope_revision_id, manifest_hash=request.manifest_hash,
                execution_attempt_id=request.execution_attempt_id,
                work_unit_id=request.work_unit_id, criterion_id=request.criterion_id,
                criterion_hash=request.criterion_hash,
                authority_hash=content_hash(replay_authority),
            ),
            payload={"evidence_id": replay_evidence_id, "evidence_hash": replay_evidence_hash, **replay_evidence},
        ))
        replay_payload = {
            "criterion_id": request.criterion_id, "criterion_hash": request.criterion_hash,
            "work_unit_id": request.work_unit_id, "execution_attempt_id": request.execution_attempt_id,
            "verification_artifact_id": request.verification_artifact_id,
            "plan_revision_id": request.plan_revision_id, "scope_revision_id": request.scope_revision_id,
            "manifest_hash": request.manifest_hash, "evidence_id": replay_evidence_id,
            "evidence_hash": replay_evidence_hash, "decision": request.decision,
            "evidence": replay_evidence,
        }
        replay_command = ProjectCommand(
            command_type=ProjectCommandType.SUBMIT_MANUAL_EVIDENCE,
            project_run_id=project_run_id, conversation_id=request.conversation_id,
            workspace_id=request.workspace_id, repository_root=run.repository_root,
            repository_root_fingerprint=request.repository_root_fingerprint,
            actor_id=request.actor_id, expected_state_version=request.expected_state_version,
            idempotency_key=request.idempotency_key, plan_revision_id=request.plan_revision_id,
            scope_revision_id=request.scope_revision_id, manifest_hash=request.manifest_hash,
            artifact_id=replay_artifact.artifact_id,
            artifact_type=replay_artifact.artifact_type.value,
            artifact_hash=replay_artifact.content_hash,
            artifact_binding_hash=replay_artifact.binding_hash,
            authority_scope=replay_authority, payload=replay_payload,
        )
        replay = self.control.replay_completed(replay_command)
        if replay is not None:
            return ProjectReadModel.model_validate(replay.read_model)
        exact = {
            "conversation_id": run.conversation_id,
            "workspace_id": run.workspace_id,
            "actor_id": run.actor_id,
            "repository_root_fingerprint": run.repository_root_fingerprint,
            "plan_revision_id": run.current_plan_revision_id,
            "scope_revision_id": run.current_scope_revision_id,
            "manifest_hash": run.current_manifest_hash,
            "work_unit_id": active_work_unit_id,
            "execution_attempt_id": active_attempt_id,
        }
        for field, expected in exact.items():
            if getattr(request, field) != expected:
                raise ProjectControlError(
                    ProjectControlErrorCode.STALE_VERIFICATION,
                    f"Manual evidence has a stale {field.replace('_', ' ')} binding.",
                )
        criterion = dict(run.verification_state.get(request.criterion_id) or {})
        if (
            criterion.get("outcome") != "manual_evidence_required"
            or criterion.get("criterion_hash") != request.criterion_hash
            or criterion.get("verification_artifact_id") != request.verification_artifact_id
        ):
            raise ProjectControlError(
                ProjectControlErrorCode.STALE_VERIFICATION,
                "Manual evidence does not target the exact pending criterion and verifier artifact.",
            )
        verifier = self.artifact_store.get(request.verification_artifact_id)
        if verifier is None or verifier.content_hash != request.verification_artifact_hash:
            raise ProjectControlError(
                ProjectControlErrorCode.STALE_VERIFICATION,
                "The verifier artifact hash is stale.",
            )
        authority = {
            "operation": "submit_manual_evidence",
            "criterion_id": request.criterion_id,
            "criterion_hash": request.criterion_hash,
            "work_unit_id": request.work_unit_id,
            "execution_attempt_id": request.execution_attempt_id,
            "verification_artifact_id": request.verification_artifact_id,
            "verification_artifact_hash": request.verification_artifact_hash,
            "authority_binding": request.authority_binding,
        }
        evidence_payload = {
            "evidence_kind": request.evidence_kind,
            "evidence": request.evidence,
            "comments": request.comments,
            "reviewer_id": request.actor_id,
        }
        evidence_hash = content_hash(evidence_payload)
        evidence_id = f"manual-{content_hash([project_run_id, request.idempotency_key, evidence_hash])[:24]}"
        artifact = self.artifact_store.put(build_project_artifact(
            artifact_type=ProjectArtifactType.MANUAL_EVIDENCE,
            binding=ProjectArtifactBinding(
                project_run_id=project_run_id,
                plan_revision_id=run.current_plan_revision_id,
                scope_revision_id=run.current_scope_revision_id,
                manifest_hash=run.current_manifest_hash,
                execution_attempt_id=active_attempt_id,
                work_unit_id=active_work_unit_id,
                criterion_id=request.criterion_id,
                criterion_hash=request.criterion_hash,
                authority_hash=content_hash(authority),
            ),
            payload={"evidence_id": evidence_id, "evidence_hash": evidence_hash, **evidence_payload},
        ))
        self.control.execute(ProjectCommand(
            command_type=ProjectCommandType.SUBMIT_MANUAL_EVIDENCE,
            project_run_id=project_run_id,
            conversation_id=request.conversation_id,
            workspace_id=request.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=request.repository_root_fingerprint,
            actor_id=request.actor_id,
            expected_state_version=request.expected_state_version,
            idempotency_key=request.idempotency_key,
            plan_revision_id=request.plan_revision_id,
            scope_revision_id=request.scope_revision_id,
            manifest_hash=request.manifest_hash,
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type.value,
            artifact_hash=artifact.content_hash,
            artifact_binding_hash=artifact.binding_hash,
            authority_scope=authority,
            payload={
                "criterion_id": request.criterion_id,
                "criterion_hash": request.criterion_hash,
                "work_unit_id": request.work_unit_id,
                "execution_attempt_id": request.execution_attempt_id,
                "verification_artifact_id": request.verification_artifact_id,
                "plan_revision_id": request.plan_revision_id,
                "scope_revision_id": request.scope_revision_id,
                "manifest_hash": request.manifest_hash,
                "evidence_id": evidence_id,
                "evidence_hash": evidence_hash,
                "decision": request.decision,
                "evidence": evidence_payload,
            },
        ))
        return self.control.get_read_model(project_run_id)

    def manual_evidence(self, project_run_id: str) -> list[dict[str, Any]]:
        return self.control.list_manual_evidence(project_run_id)

    def list_artifacts(self, project_run_id: str, *, limit: int = 100) -> list[ProjectArtifact]:
        # Loading the project first prevents cross-kind artifact enumeration.
        self.control.get_project(project_run_id)
        return self.artifact_store.list_for_project(project_run_id, limit=limit)

    def _put_artifact(
        self,
        run: ProjectRun,
        artifact_type: ProjectArtifactType,
        payload: dict[str, Any],
        *,
        revision_number: int,
        manifest_hash: str | None = None,
    ) -> ProjectArtifact:
        artifact = build_project_artifact(
            artifact_type=artifact_type,
            binding=ProjectArtifactBinding(
                project_run_id=run.project_run_id,
                plan_revision_id=run.current_plan_revision_id,
                scope_revision_id=run.current_scope_revision_id,
                manifest_hash=manifest_hash or run.current_manifest_hash,
            ),
            payload=payload,
            revision_number=revision_number,
        )
        return self.artifact_store.put(artifact)

    def _existing_artifact_replay(
        self,
        run: ProjectRun,
        idempotency_key: str,
        artifact_type: ProjectArtifactType,
        payload: dict[str, Any],
        revision_number: int,
    ) -> ProjectReadModel | None:
        if not self.control.has_idempotency_key(run.project_run_id, idempotency_key):
            return None
        matches = any(
            artifact.payload == payload
            and artifact.revision_number == revision_number
            for artifact in self.artifact_store.list_for_project(
                run.project_run_id, artifact_type=artifact_type
            )
        )
        if not matches:
            raise ProjectControlError(
                ProjectControlErrorCode.IDEMPOTENCY_CONFLICT,
                "This idempotency key is already bound to different artifact content.",
            )
        return self.control.get_read_model(run.project_run_id)

    @staticmethod
    def _base(**values: str) -> dict[str, str]:
        return values

    def _artifact_command(
        self,
        run: ProjectRun,
        command_type: ProjectCommandType,
        artifact: ProjectArtifact,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        expected_state_version: int | None = None,
    ) -> ProjectCommand:
        return ProjectCommand(
            command_type=command_type,
            project_run_id=run.project_run_id,
            conversation_id=run.conversation_id,
            workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=run.actor_id,
            expected_state_version=expected_state_version or run.state_version,
            idempotency_key=idempotency_key,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type.value,
            artifact_hash=artifact.content_hash,
            artifact_binding_hash=artifact.binding_hash,
            payload=payload,
        )

    @staticmethod
    def _validate_folder_authority(
        authority: dict[str, Any],
        *,
        conversation_id: str,
        workspace_id: str,
        repository_root: str,
        repository_root_fingerprint: str,
    ) -> None:
        expected = {
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "action_id": workspace_id,
            "repository_root_fingerprint": repository_root_fingerprint,
        }
        if str(authority.get("status") or "") != "completed" or any(
            str(authority.get(key) or "") != value for key, value in expected.items()
        ):
            raise ProjectControlError(
                ProjectControlErrorCode.WORKSPACE_MISMATCH,
                "Canonical project creation requires completed folder authority bound to this conversation and repository.",
            )
        authority_root = authority.get("repository_root")
        if authority_root is not None and str(Path(str(authority_root)).resolve()) != repository_root:
            raise ProjectControlError(
                ProjectControlErrorCode.REPOSITORY_ROOT_MISMATCH,
                "Canonical project creation targets a different repository than the completed folder authority.",
            )
