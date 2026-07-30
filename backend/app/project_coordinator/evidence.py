from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from backend.app.project_control.contracts import canonical_json, content_hash
from backend.app.project_coordinator.contracts import CoordinatorIntent


MAX_COORDINATOR_EVIDENCE_BYTES = 131_072
_SECRET_KEYS = frozenset({
    "access_token", "api_key", "authorization", "cookie", "password",
    "private_key", "secret", "token",
})


class CoordinatorEvidenceError(ValueError):
    pass


def build_work_unit_evidence(
    *,
    intent: CoordinatorIntent,
    run,
    plan,
    work_unit: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "project_run_id": run.project_run_id,
        "workspace_id": run.workspace_id,
        "repository_root_fingerprint": run.repository_root_fingerprint,
        "coordinator_intent_id": intent.coordinator_intent_id,
        "plan_revision_id": intent.plan_revision_id,
        "scope_revision_id": intent.scope_revision_id,
        "manifest_hash": intent.manifest_hash,
        "expected_project_state_version": intent.expected_project_state_version,
        "work_unit": _redact(work_unit),
        "acceptance_criteria": _redact(list(plan.acceptance_criteria)),
        "current_artifact_ids": dict(run.current_artifact_ids),
        "current_artifact_hashes": dict(run.current_artifact_hashes),
    }
    return _bounded(payload)


def build_handoff_evidence(*, intent: CoordinatorIntent, run, plan) -> dict[str, Any]:
    payload = {
        "project_run_id": run.project_run_id,
        "coordinator_intent_id": intent.coordinator_intent_id,
        "plan_revision_id": intent.plan_revision_id,
        "scope_revision_id": intent.scope_revision_id,
        "final_manifest_hash": intent.manifest_hash,
        "work_unit_state": _redact(run.work_unit_state),
        "verification_state": _redact(run.verification_state),
        "required_acceptance_criteria": [
            _redact(item)
            for item in plan.acceptance_criteria
            if item.get("required", True) is not False
        ],
        "artifact_ids": dict(run.current_artifact_ids),
        "artifact_hashes": dict(run.current_artifact_hashes),
    }
    return _bounded(payload)


def validate_patch_operations(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise CoordinatorEvidenceError(
            "The approved work unit has no deterministic patch operations."
        )
    if len(value) > 100:
        raise CoordinatorEvidenceError("The patch operation count exceeds the limit.")
    operations: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise CoordinatorEvidenceError("Patch operations must be objects.")
        operation = _redact(raw)
        path = str(operation.get("path") or operation.get("relative_path") or "")
        normalized = path.replace("\\", "/")
        candidate = PurePosixPath(normalized)
        if (
            not normalized
            or candidate.is_absolute()
            or ".." in candidate.parts
            or normalized.startswith("/")
        ):
            raise CoordinatorEvidenceError("Patch operations require safe relative paths.")
        operation["path"] = candidate.as_posix()
        operation.pop("relative_path", None)
        operations.append(operation)
    _bounded({"operations": operations})
    return tuple(operations)


def evidence_reference(payload: dict[str, Any]) -> dict[str, str]:
    return {"evidence_hash": content_hash(payload)}


def _bounded(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        size = len(canonical_json(payload).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise CoordinatorEvidenceError("Coordinator evidence must be canonical JSON.") from exc
    if size > MAX_COORDINATOR_EVIDENCE_BYTES:
        raise CoordinatorEvidenceError("Coordinator evidence exceeds the byte limit.")
    return payload


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if str(key).lower() in _SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return value[:32_768]
    return value


__all__ = [
    "CoordinatorEvidenceError",
    "MAX_COORDINATOR_EVIDENCE_BYTES",
    "build_handoff_evidence",
    "build_work_unit_evidence",
    "evidence_reference",
    "validate_patch_operations",
]
