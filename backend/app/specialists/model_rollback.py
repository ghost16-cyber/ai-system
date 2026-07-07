from __future__ import annotations

import joblib
from pathlib import Path
from typing import Any

from .model_audit_logger import append_model_audit_event
from .model_quality_gate import evaluate_promotion_quality_gate
from .model_store import (
    DEFAULT_SPECIALIST_MODEL_DIR,
    deactivate_specialist_model,
    find_specialist_model,
    specialist_artifact_path,
    validate_model_metadata,
)


def rollback_specialist_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(model_dir or DEFAULT_SPECIALIST_MODEL_DIR)
    located = find_specialist_model(model_id, root)
    if located is None:
        result = {"rolled_back": False, "reason": f"Model not found: {model_id}"}
        append_model_audit_event(action="model_rollback_failed", model_id=model_id, details=result)
        return result

    artifact = located["artifact"]
    metadata = artifact["metadata"]
    specialist = metadata.get("specialist")
    blocked_reason = _rollback_block_reason(metadata)
    if blocked_reason:
        result = {
            "rolled_back": False,
            "model_id": model_id,
            "specialist": specialist,
            "reason": blocked_reason,
        }
        append_model_audit_event(
            action="model_rollback_failed",
            model_id=model_id,
            specialist=specialist,
            details={"reason": blocked_reason},
        )
        return result

    existing = None
    from .model_store import load_specialist_model

    existing = load_specialist_model(specialist, root)
    deactivated_existing = None
    if existing is not None and existing["metadata"]["model_id"] == model_id:
        result = {
            "rolled_back": False,
            "model_id": model_id,
            "specialist": specialist,
            "reason": "Selected model is already the active promoted model.",
        }
        append_model_audit_event(
            action="model_rollback_failed",
            model_id=model_id,
            specialist=specialist,
            details={"reason": result["reason"]},
        )
        return result
    if existing is not None:
        deactivated_existing = deactivate_specialist_model(existing["metadata"]["model_id"], root)

    now_metadata = {
        **metadata,
        "lifecycle_status": "promoted",
        "rolled_back_at": _metadata_timestamp(metadata),
        "updated_at": _metadata_timestamp(metadata),
    }
    promoted_path = specialist_artifact_path(specialist, root)
    promoted_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": artifact["pipeline"], "metadata": now_metadata}, promoted_path)
    Path(located["path"]).unlink(missing_ok=True)

    result = {
        "rolled_back": True,
        "model_id": model_id,
        "specialist": specialist,
        "path": str(promoted_path),
        "metadata": now_metadata,
        "deactivated_existing": deactivated_existing,
    }
    append_model_audit_event(
        action="model_rolled_back",
        model_id=model_id,
        specialist=specialist,
        details={"deactivated_existing": deactivated_existing},
    )
    return result


def _rollback_block_reason(metadata: dict[str, Any]) -> str | None:
    if not validate_model_metadata(metadata, require_promoted=False):
        return "Model metadata failed validation."
    status = metadata.get("lifecycle_status")
    if status == "rejected":
        return "Rejected models cannot be rolled back."
    if status == "promoted":
        return "Selected model is already the active promoted model."
    if not isinstance(metadata.get("metrics"), dict):
        return "Rollback blocked: model has no stored metrics."
    gate_metadata = {**metadata, "lifecycle_status": "candidate"}
    gate = evaluate_promotion_quality_gate(gate_metadata)
    if gate["passed"] is not True:
        return "; ".join(gate["failures"])
    return None


def _metadata_timestamp(metadata: dict[str, Any]) -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
