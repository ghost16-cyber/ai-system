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
        if action != "cancel_project" and pending.split(":", 1)[0] != action:
            raise ProjectControlError(ProjectControlErrorCode.ILLEGAL_TRANSITION, "This action is not currently permitted.")
        if action == "cancel_project" and run.lifecycle_status.value in {"cancelled", "completed"}:
            raise ProjectControlError(ProjectControlErrorCode.TERMINAL_PROJECT, "A terminal project cannot be cancelled again.")
        if action != "cancel_project":
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
            authority_scope=authority,
            payload=payload,
        ))
        return self.control.get_read_model(project_run_id)

    def list_projects(self, conversation_id: str) -> list[ProjectReadModel]:
        return self.control.list_projects_for_conversation(conversation_id)

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
