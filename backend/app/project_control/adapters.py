from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.project_control.contracts import (
    ApprovalType,
    ProjectCommand,
    ProjectCommandType,
    ProjectLifecycle,
    ProjectReadModel,
    content_hash,
)
from backend.app.project_control.errors import ProjectControlError, ProjectControlErrorCode
from backend.app.project_control.service import ProjectControlPlane


class ProjectDeliveryControlAdapter:
    """Compatibility boundary: legacy evidence in, canonical commands out."""

    def __init__(self, control: ProjectControlPlane) -> None:
        self.control = control

    def ensure(self, job: dict[str, Any], root: str | Path, *, migrated: bool) -> ProjectReadModel:
        return self.control.reconcile_legacy_delivery(
            job, migrated=migrated, repository_root=str(Path(root).resolve()),
        )

    def decorate(self, job: dict[str, Any], root: str | Path, *, migrated: bool = True) -> dict[str, Any]:
        read_model = self.ensure(job, root, migrated=migrated)
        return {**job, "project_control": read_model.model_dump(mode="json")}

    def apply_transition(
        self,
        current: dict[str, Any],
        updated: dict[str, Any],
        root: str | Path,
        operation: str,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectReadModel:
        metadata = dict(metadata or {})
        self.ensure(current, root, migrated=True)
        run = self.control.get_project(str(current["delivery_job_id"]))
        prefix = str(metadata.get("idempotency_key") or f"legacy:{operation}:{content_hash(metadata)[:20]}")
        if operation == "plan_approval_granted":
            self._execute(run, ProjectCommandType.APPROVE_PLAN, prefix, authority={
                "operation": "prepare_work_units",
                "plan_hash": str((updated.get("plan") or {}).get("plan_hash") or ""),
            })
        elif operation in {"patch_preview", "stage8_repair_preview"}:
            work_unit_id = str(updated.get("active_work_unit_id") or metadata.get("work_unit_id") or "")
            if run.lifecycle_status == ProjectLifecycle.READY_FOR_WORK:
                self._execute(run, ProjectCommandType.BEGIN_WORK_UNIT, f"{prefix}:begin", payload={"work_unit_id": work_unit_id}, authority={"work_unit_id": work_unit_id})
                run = self.control.get_project(run.project_run_id)
            self._execute(run, ProjectCommandType.RECORD_PATCH_PREVIEW, f"{prefix}:preview", payload={"patch_id": str(metadata.get("patch_id") or "")})
        elif operation == "patch_application":
            patch_id = str(metadata.get("patch_id") or "")
            self._execute(run, ProjectCommandType.RECORD_PATCH_RESULT, prefix, payload={
                "patch_id": patch_id, "succeeded": True,
                "resulting_manifest_hash": str(updated.get("project_state_hash") or (updated.get("project_state_manifest") or {}).get("manifest_hash") or ""),
                "result_reference": {"patch_id": patch_id},
            }, authority={"patch_id": patch_id})
        elif operation == "command_plan":
            self._execute(run, ProjectCommandType.RECORD_COMMAND_PREVIEW, prefix, payload={
                "command_id": str(metadata.get("plan_id") or ""),
            })
        elif operation == "verifier_completion":
            self._record_verification(run, updated, prefix, metadata)
        elif operation in {"scope_revision", "engagement_scope_change", "repair_scope_change", "scope_change_detection"}:
            self._revise(run, updated, prefix, operation)
        elif operation == "rollback_execution":
            self._execute(run, ProjectCommandType.RECORD_ROLLBACK, prefix, payload={
                "patch_id": str(metadata.get("patch_id") or ""), "succeeded": True,
                "resulting_manifest_hash": str(updated.get("project_state_hash") or ""),
            })
        elif operation == "handoff_generation":
            final_hash = str(updated.get("project_state_hash") or (updated.get("project_state_manifest") or {}).get("manifest_hash") or "")
            self._execute(run, ProjectCommandType.REQUEST_HANDOFF, f"{prefix}:request", payload={"final_manifest_hash": final_hash})
            run = self.control.get_project(run.project_run_id)
            self._execute(run, ProjectCommandType.FINALIZE_PROJECT, f"{prefix}:handoff")
            run = self.control.get_project(run.project_run_id)
            self._execute(run, ProjectCommandType.FINALIZE_PROJECT, f"{prefix}:complete")
        elif operation == "cancellation":
            self._execute(run, ProjectCommandType.CANCEL_PROJECT, prefix, payload={"reason": "Cancelled through the legacy delivery endpoint."})
        elif operation in {"limit_reached", "stage8_diagnosis"}:
            self._execute(run, ProjectCommandType.MARK_BLOCKED, prefix, payload={"reason": operation})
        elif operation == "clarification_response" and run.lifecycle_status == ProjectLifecycle.PLANNING:
            self._propose_plan(run, updated, prefix)
        return self.control.get_read_model(run.project_run_id)

    def approve_plan_bound(
        self,
        job: dict[str, Any],
        root: str | Path,
        *,
        plan_hash: str,
        idempotency_key: str,
        expected_state_version: int | None,
        plan_revision_id: str | None,
        scope_revision_id: str | None,
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        displayed_hash = str((job.get("plan") or {}).get("plan_hash") or "")
        if plan_hash != displayed_hash and not self.control.has_idempotency_key(str(job["delivery_job_id"]), idempotency_key):
            raise ProjectControlError(ProjectControlErrorCode.PLAN_REVISION_MISMATCH, "The approved plan hash is not the displayed immutable plan.")
        run = self.control.get_project(str(job["delivery_job_id"]))
        self.control.execute(ProjectCommand(
            command_type=ProjectCommandType.APPROVE_PLAN,
            project_run_id=run.project_run_id, conversation_id=run.conversation_id,
            workspace_id=run.workspace_id, repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint, actor_id=run.actor_id,
            expected_state_version=expected_state_version or run.state_version,
            idempotency_key=idempotency_key,
            plan_revision_id=plan_revision_id or run.current_plan_revision_id,
            scope_revision_id=scope_revision_id or run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            authority_scope={"operation": "prepare_work_units", "plan_hash": plan_hash},
        ))
        return self.control.get_read_model(run.project_run_id)

    def approve_patch(self, job: dict[str, Any], root: str | Path, patch_id: str) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        self._execute(run, ProjectCommandType.APPROVE_PATCH, f"legacy:patch-approval:{patch_id}",
                      payload={"patch_id": patch_id}, authority={"patch_id": patch_id, "operation": "apply_exact_patch"})
        return self.control.get_read_model(run.project_run_id)

    def begin_patch_application(
        self,
        job: dict[str, Any],
        root: str | Path,
        patch_id: str,
        *,
        worker_dispatch: dict[str, Any] | None = None,
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        payload: dict[str, Any] = {"patch_id": patch_id}
        if worker_dispatch is not None:
            payload["worker_dispatch"] = worker_dispatch
        self._execute(run, ProjectCommandType.BEGIN_PATCH_APPLICATION, f"legacy:patch-start:{patch_id}",
                      payload=payload, authority={"patch_id": patch_id, "operation": "apply_exact_patch"})
        return self.control.get_read_model(run.project_run_id)

    def approve_command(
        self,
        job: dict[str, Any],
        root: str | Path,
        command_id: str,
        *,
        execution_hash: str | None = None,
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        authority = {"command_id": command_id, "operation": "execute_exact_command"}
        payload: dict[str, Any] = {"command_id": command_id}
        if execution_hash is not None:
            authority["execution_hash"] = execution_hash
            payload["execution_hash"] = execution_hash
        self._execute(run, ProjectCommandType.APPROVE_COMMAND, f"legacy:command-approval:{command_id}",
                      payload=payload, authority=authority)
        return self.control.get_read_model(run.project_run_id)

    def begin_command_execution(
        self,
        job: dict[str, Any],
        root: str | Path,
        command_id: str,
        *,
        execution_hash: str | None = None,
        worker_dispatch: dict[str, Any] | None = None,
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        payload: dict[str, Any] = {"command_id": command_id}
        authority = {"command_id": command_id}
        if execution_hash is not None:
            payload["execution_hash"] = execution_hash
            authority["execution_hash"] = execution_hash
        if worker_dispatch is not None:
            payload["worker_dispatch"] = worker_dispatch
            for attempt in reversed(self.control.list_attempts(run.project_run_id)):
                if (
                    attempt.attempt_type.value == "command_execution"
                    and str(attempt.authority.get("command_id") or "") == command_id
                    and str(attempt.authority.get("execution_hash") or "") == execution_hash
                ):
                    return self.control.get_read_model(run.project_run_id)
        self._execute(run, ProjectCommandType.BEGIN_COMMAND_EXECUTION, f"legacy:command-start:{command_id}",
                      payload=payload, authority=authority)
        return self.control.get_read_model(run.project_run_id)

    def record_rollback_preview(
        self,
        job: dict[str, Any],
        root: str | Path,
        rollback_id: str,
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        self._execute(
            run,
            ProjectCommandType.RECORD_ROLLBACK_PREVIEW,
            f"legacy:rollback-preview:{rollback_id}",
            payload={"rollback_id": rollback_id},
            authority={"rollback_id": rollback_id},
        )
        return self.control.get_read_model(run.project_run_id)

    def approve_rollback(
        self,
        job: dict[str, Any],
        root: str | Path,
        rollback_id: str,
        *,
        mutation_spec_hash: str,
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        self._execute(
            run,
            ProjectCommandType.APPROVE_ROLLBACK,
            f"legacy:rollback-approval:{rollback_id}",
            payload={
                "rollback_id": rollback_id,
                "mutation_spec_hash": mutation_spec_hash,
            },
            authority={
                "rollback_id": rollback_id,
                "mutation_spec_hash": mutation_spec_hash,
            },
        )
        return self.control.get_read_model(run.project_run_id)

    def begin_rollback(
        self,
        job: dict[str, Any],
        root: str | Path,
        rollback_id: str,
        *,
        mutation_spec_hash: str,
        worker_dispatch: dict[str, Any],
    ) -> ProjectReadModel:
        self.ensure(job, root, migrated=True)
        run = self.control.get_project(str(job["delivery_job_id"]))
        for attempt in reversed(self.control.list_attempts(run.project_run_id)):
            if (
                attempt.attempt_type.value == "rollback"
                and str(attempt.authority.get("rollback_id") or "") == rollback_id
                and str(attempt.authority.get("mutation_spec_hash") or "")
                == mutation_spec_hash
            ):
                return self.control.get_read_model(run.project_run_id)
        self._execute(
            run,
            ProjectCommandType.BEGIN_ROLLBACK,
            f"legacy:rollback-start:{rollback_id}",
            payload={
                "rollback_id": rollback_id,
                "mutation_spec_hash": mutation_spec_hash,
                "worker_dispatch": worker_dispatch,
            },
            authority={
                "rollback_id": rollback_id,
                "mutation_spec_hash": mutation_spec_hash,
            },
        )
        return self.control.get_read_model(run.project_run_id)

    def _record_verification(self, run, updated: dict[str, Any], prefix: str, metadata: dict[str, Any]) -> None:
        command_id = str(metadata.get("plan_id") or "")
        succeeded = str(metadata.get("exit_code") or "0") == "0" and str(metadata.get("status") or "") != "failed"
        if run.lifecycle_status == ProjectLifecycle.WORK_IN_PROGRESS and command_id:
            self._execute(run, ProjectCommandType.RECORD_COMMAND_RESULT, f"{prefix}:command", payload={
                "command_id": command_id, "succeeded": succeeded,
                "result_reference": {"command_id": command_id},
            }, authority={"command_id": command_id})
            run = self.control.get_project(run.project_run_id)
            if not succeeded:
                return
        if run.lifecycle_status in {ProjectLifecycle.WORK_IN_PROGRESS, ProjectLifecycle.READY_FOR_WORK}:
            self._execute(run, ProjectCommandType.REQUEST_VERIFICATION, f"{prefix}:request", authority={"criterion_id": str(metadata.get("criterion_id") or "")})
            run = self.control.get_project(run.project_run_id)
        results = [item for item in updated.get("verifier_results") or [] if isinstance(item, dict)]
        result = results[-1] if results else {}
        criterion_id = str(metadata.get("criterion_id") or result.get("criterion_id") or "")
        plan = self.control.get_plan_revision(str(run.current_plan_revision_id))
        criterion = next((item for item in plan.acceptance_criteria if str(item.get("criterion_id") or item.get("id")) == criterion_id), None)
        if criterion is None:
            raise ProjectControlError(ProjectControlErrorCode.STALE_VERIFICATION, "The legacy verifier criterion is not in the canonical plan.")
        outcome = str(result.get("outcome") or ("passed" if succeeded else "failed"))
        self._execute(run, ProjectCommandType.RECORD_VERIFIER_RESULT, f"{prefix}:result", payload={
            "criterion_id": criterion_id, "outcome": outcome,
            "result_hash": str(result.get("result_hash") or content_hash(result)),
            "criterion_hash": content_hash(criterion),
            "plan_revision_id": run.current_plan_revision_id,
            "scope_revision_id": run.current_scope_revision_id,
            "manifest_hash": str(result.get("input_manifest_hash") or run.current_manifest_hash or ""),
            "result_reference": {"verifier_result_id": str(result.get("verifier_result_id") or "")},
        })
        run = self.control.get_project(run.project_run_id)
        active = str(updated.get("active_work_unit_id") or "")
        runtime = next((item for item in updated.get("work_unit_execution_states") or [] if item.get("work_unit_id") == active), {})
        if active and str(runtime.get("status") or "") in {"completed", "satisfied"} and outcome == "passed":
            self._execute(run, ProjectCommandType.COMPLETE_WORK_UNIT, f"{prefix}:complete-unit", payload={"work_unit_id": active})

    def _revise(self, run, updated: dict[str, Any], prefix: str, reason: str) -> None:
        specification = dict(updated.get("specification") or {})
        plan = dict(updated.get("plan_revision") or updated.get("plan") or {})
        included = sorted({str(path) for item in plan.get("work_units", []) for path in item.get("expected_files", [])})
        self._execute(run, ProjectCommandType.REVISE_SCOPE, f"{prefix}:scope", payload={
            "specification_hash": str(specification.get("specification_hash") or run.specification_hash or ""),
            "included_paths": included, "excluded_paths": list(specification.get("explicit_exclusions") or []),
            "allowed_operations": ["read", "patch_preview", "approved_patch", "approved_command", "verification"],
            "reason": reason,
        })
        run = self.control.get_project(run.project_run_id)
        self._propose_plan(run, updated, f"{prefix}:plan")

    def _propose_plan(self, run, updated: dict[str, Any], key: str) -> None:
        specification = dict(updated.get("specification") or {})
        plan = dict(updated.get("plan_revision") or updated.get("plan") or {})
        self._execute(run, ProjectCommandType.PROPOSE_PLAN_REVISION, key, payload={
            "acceptance_criteria": list(specification.get("acceptance_criteria") or []),
            "work_units": list(plan.get("work_units") or []),
            "configured_limits": dict(updated.get("limits") or {}),
        })

    def _execute(self, run, kind: ProjectCommandType, key: str, *, payload=None, authority=None):
        return self.control.execute(ProjectCommand(
            command_type=kind, project_run_id=run.project_run_id,
            conversation_id=run.conversation_id, workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=run.actor_id, expected_state_version=run.state_version,
            idempotency_key=key, plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            authority_scope=authority or {}, payload=payload or {},
        ))


__all__ = ["ProjectDeliveryControlAdapter"]
