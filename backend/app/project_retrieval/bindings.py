from __future__ import annotations

from pathlib import Path

from backend.app.project_control import ProjectControlPlane
from backend.app.project_control.contracts import ProjectRun, content_hash
from backend.app.project_retrieval.contracts import ProjectRetrievalBinding


class RetrievalBindingError(ValueError):
    pass


def canonical_retrieval_authority_id(run: ProjectRun) -> str:
    return content_hash({
        "authority_kind": "canonical_project_advisory_retrieval",
        "project_run_id": run.project_run_id,
        "actor_id": run.actor_id,
        "conversation_id": run.conversation_id,
        "workspace_id": run.workspace_id,
        "repository_root_fingerprint": run.repository_root_fingerprint,
    })


def validate_project_binding(
    control: ProjectControlPlane,
    binding: ProjectRetrievalBinding,
) -> tuple[ProjectRun, object, object | None]:
    run = control.get_project(binding.project_id)
    checks = (
        (run.conversation_id, binding.conversation_id, "conversation_mismatch"),
        (run.actor_id, binding.actor_id, "actor_mismatch"),
        (run.workspace_id, binding.workspace_id, "workspace_mismatch"),
        (str(Path(run.repository_root).resolve()), str(Path(binding.repository_root).resolve()), "repository_root_mismatch"),
        (run.state_version, binding.expected_project_state_version, "stale_project_state_version"),
        (run.current_scope_revision_id, binding.scope_revision_id, "stale_scope"),
        (run.current_manifest_hash, binding.repository_manifest_hash, "manifest_mismatch"),
        (canonical_retrieval_authority_id(run), binding.authority_id, "authority_mismatch"),
    )
    for actual, expected, code in checks:
        if actual != expected:
            raise RetrievalBindingError(code)
    if run.terminal_at is not None:
        raise RetrievalBindingError("project_terminal")
    scope = control.get_scope_revision(binding.scope_revision_id)
    if scope.project_run_id != run.project_run_id or scope.content_hash != binding.scope_hash:
        raise RetrievalBindingError("stale_scope")
    if run.current_plan_revision_id is None:
        if binding.plan_revision_id is not None or binding.plan_hash is not None:
            raise RetrievalBindingError("stale_plan")
        plan = None
    else:
        if binding.plan_revision_id != run.current_plan_revision_id:
            raise RetrievalBindingError("stale_plan")
        plan = control.get_plan_revision(run.current_plan_revision_id)
        if plan.content_hash != binding.plan_hash:
            raise RetrievalBindingError("stale_plan")
    return run, scope, plan

