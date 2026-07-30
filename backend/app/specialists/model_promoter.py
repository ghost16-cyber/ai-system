from __future__ import annotations

from pathlib import Path
from typing import Any

from .model_audit_logger import append_model_audit_event
from .model_store import (
    deactivate_specialist_model,
    promote_specialist_model,
    reject_specialist_model,
)


def promote_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    result = promote_specialist_model(model_id, model_dir)
    if result.get("promoted") is True:
        append_model_audit_event(
            action="model_promoted",
            model_id=result.get("model_id"),
            specialist=result.get("specialist"),
            details={"path": result.get("path"), "reason": result.get("reason")},
        )
    else:
        append_model_audit_event(
            action="model_promotion_blocked",
            model_id=result.get("model_id") or model_id,
            specialist=result.get("specialist"),
            details={
                "reason": result.get("reason"),
                "promotion_quality_gate": result.get("promotion_quality_gate"),
            },
        )
    return result


def deactivate_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    result = deactivate_specialist_model(model_id, model_dir)
    if result.get("deactivated") is True:
        append_model_audit_event(
            action="model_deactivated",
            model_id=result.get("model_id"),
            specialist=result.get("specialist"),
            details={"path": result.get("path")},
        )
    return result


def reject_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    result = reject_specialist_model(model_id, model_dir)
    if result.get("rejected") is True:
        append_model_audit_event(
            action="model_rejected",
            model_id=result.get("model_id"),
            specialist=result.get("specialist"),
            details={"path": result.get("path")},
        )
    return result
