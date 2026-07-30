from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib

from .model_quality_gate import evaluate_promotion_quality_gate


DEFAULT_SPECIALIST_MODEL_DIR = Path("models/specialists")
MODEL_STORE_VERSION = "sklearn_tfidf_logreg_v1"
MODEL_LIFECYCLE_STATUSES = {"candidate", "promoted", "deactivated", "rejected"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def specialist_artifact_path(
    specialist: str,
    model_dir: str | Path | None = None,
) -> Path:
    safe_name = _safe_name(specialist)
    return Path(model_dir or DEFAULT_SPECIALIST_MODEL_DIR) / "promoted" / f"{safe_name}.joblib"


def lifecycle_artifact_path(
    model_id: str,
    lifecycle_status: str,
    model_dir: str | Path | None = None,
) -> Path:
    status = _normalize_lifecycle_status(lifecycle_status)
    return Path(model_dir or DEFAULT_SPECIALIST_MODEL_DIR) / status / f"{_safe_name(model_id)}.joblib"


def build_model_metadata(
    *,
    specialist: str,
    accuracy: float,
    label_counts: dict[str, int],
    train_examples: int,
    test_examples: int,
    quality_gate: dict[str, Any],
    model_id: str | None = None,
    lifecycle_status: str = "candidate",
    dataset_id: str | None = None,
    training_job_id: str | None = None,
    extra_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    metrics = {
        "accuracy": accuracy,
        "label_counts": label_counts,
        "quality_gate": quality_gate,
        "train_examples": train_examples,
        "test_examples": test_examples,
        **(extra_metrics or {}),
    }
    return {
        "model_id": model_id or _new_model_id(specialist),
        "specialist": specialist,
        "model_type": "sklearn_pipeline",
        "model_version": MODEL_STORE_VERSION,
        "vectorizer": "TfidfVectorizer",
        "classifier": "LogisticRegression",
        "accuracy": accuracy,
        "label_counts": label_counts,
        "train_examples": train_examples,
        "test_examples": test_examples,
        "quality_gate": quality_gate,
        "metrics": metrics,
        "dataset_id": dataset_id,
        "training_job_id": training_job_id,
        "lifecycle_status": _normalize_lifecycle_status(lifecycle_status),
        "created_at": now,
        "updated_at": now,
        "advisory_only": True,
    }


def save_specialist_model(
    *,
    specialist: str,
    pipeline: Any,
    metadata: dict[str, Any],
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    if metadata.get("advisory_only") is not True:
        raise ValueError("specialist model metadata must be advisory_only=true")
    lifecycle_status = _normalize_lifecycle_status(metadata.get("lifecycle_status", "candidate"))
    if metadata.get("quality_gate", {}).get("passed") is not True and lifecycle_status != "rejected":
        raise ValueError("specialist model must pass the quality gate before it is saved")

    metadata = {
        **metadata,
        "model_id": metadata.get("model_id") or _new_model_id(specialist),
        "lifecycle_status": lifecycle_status,
        "updated_at": _utc_now(),
    }
    metadata.setdefault("created_at", metadata["updated_at"])

    path = _path_for_metadata(metadata, model_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipeline, "metadata": metadata}, path)
    return {"saved": True, "path": str(path), "metadata": metadata}


def load_specialist_model(
    specialist: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    path = specialist_artifact_path(specialist, model_dir)
    if not path.exists():
        return None

    try:
        artifact = joblib.load(path)
    except Exception:
        return None
    if not isinstance(artifact, dict):
        return None
    if not validate_model_metadata(artifact.get("metadata"), specialist, require_promoted=True):
        return None
    if "pipeline" not in artifact:
        return None
    return {"path": str(path), **artifact}


def validate_model_metadata(
    metadata: Any,
    specialist: str | None = None,
    *,
    require_promoted: bool = True,
) -> bool:
    if not isinstance(metadata, dict):
        return False
    if not metadata.get("model_id"):
        return False
    if metadata.get("model_version") != MODEL_STORE_VERSION:
        return False
    if metadata.get("model_type") != "sklearn_pipeline":
        return False
    if metadata.get("advisory_only") is not True:
        return False
    if specialist and metadata.get("specialist") != specialist:
        return False
    lifecycle_status = metadata.get("lifecycle_status")
    if lifecycle_status not in MODEL_LIFECYCLE_STATUSES:
        return False
    if require_promoted and lifecycle_status != "promoted":
        return False
    quality_gate = metadata.get("quality_gate")
    if not isinstance(quality_gate, dict):
        return False
    if require_promoted and quality_gate.get("passed") is not True:
        return False
    return True


def list_specialist_models(model_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(model_dir or DEFAULT_SPECIALIST_MODEL_DIR)
    models: list[dict[str, Any]] = []
    if not root.exists():
        return {"model_dir": str(root), "models": models}

    for path in sorted(root.glob("*/*.joblib")):
        artifact = _load_artifact_path(path)
        if artifact is None:
            models.append({"path": str(path), "valid": False})
            continue
        metadata = artifact["metadata"]
        models.append(
            {
                "model_id": metadata["model_id"],
                "specialist": metadata["specialist"],
                "path": str(path),
                "lifecycle_status": metadata["lifecycle_status"],
                "valid": validate_model_metadata(metadata, require_promoted=False),
                "active": metadata["lifecycle_status"] == "promoted",
                "metadata": metadata,
            }
        )
    return {"model_dir": str(root), "models": models}


def promote_specialist_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    located = find_specialist_model(model_id, model_dir)
    if located is None:
        return {"promoted": False, "reason": f"Model not found: {model_id}"}

    artifact = located["artifact"]
    metadata = artifact["metadata"]
    if not validate_model_metadata(metadata, require_promoted=False):
        return {"promoted": False, "reason": "Model metadata failed validation."}
    if metadata["lifecycle_status"] == "promoted":
        return {
            "promoted": True,
            "model_id": metadata["model_id"],
            "specialist": metadata["specialist"],
            "path": located["path"],
            "metadata": metadata,
            "reason": "Model is already promoted.",
        }
    if metadata["lifecycle_status"] == "rejected":
        return {"promoted": False, "reason": "Rejected models cannot be promoted."}
    if metadata["lifecycle_status"] != "candidate":
        return {
            "promoted": False,
            "reason": f"Only candidate models can be promoted; found {metadata['lifecycle_status']}.",
        }
    promotion_gate = evaluate_promotion_quality_gate(metadata)
    if promotion_gate["passed"] is not True:
        return {
            "promoted": False,
            "model_id": metadata["model_id"],
            "specialist": metadata["specialist"],
            "reason": "; ".join(promotion_gate["failures"]),
            "promotion_quality_gate": promotion_gate,
        }

    root = Path(model_dir or DEFAULT_SPECIALIST_MODEL_DIR)
    existing = load_specialist_model(metadata["specialist"], root)
    deactivated_existing: dict[str, Any] | None = None
    if existing is not None and existing["metadata"]["model_id"] != metadata["model_id"]:
        deactivated_existing = deactivate_specialist_model(existing["metadata"]["model_id"], root)

    promoted_metadata = {
        **metadata,
        "lifecycle_status": "promoted",
        "promoted_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    promoted_path = specialist_artifact_path(promoted_metadata["specialist"], root)
    promoted_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": artifact["pipeline"], "metadata": promoted_metadata}, promoted_path)
    _remove_path(Path(located["path"]))
    return {
        "promoted": True,
        "model_id": promoted_metadata["model_id"],
        "specialist": promoted_metadata["specialist"],
        "path": str(promoted_path),
        "metadata": promoted_metadata,
        "deactivated_existing": deactivated_existing,
    }


def deactivate_specialist_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    located = find_specialist_model(model_id, model_dir)
    if located is None:
        return {"deactivated": False, "reason": f"Model not found: {model_id}"}

    artifact = located["artifact"]
    metadata = artifact["metadata"]
    if metadata["lifecycle_status"] != "promoted":
        return {
            "deactivated": False,
            "reason": f"Only promoted models can be deactivated; found {metadata['lifecycle_status']}.",
        }
    deactivated_metadata = {
        **metadata,
        "lifecycle_status": "deactivated",
        "deactivated_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    deactivated_path = lifecycle_artifact_path(deactivated_metadata["model_id"], "deactivated", model_dir)
    deactivated_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": artifact["pipeline"], "metadata": deactivated_metadata}, deactivated_path)
    _remove_path(Path(located["path"]))
    return {
        "deactivated": True,
        "model_id": deactivated_metadata["model_id"],
        "specialist": deactivated_metadata["specialist"],
        "path": str(deactivated_path),
        "metadata": deactivated_metadata,
    }


def reject_specialist_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    located = find_specialist_model(model_id, model_dir)
    if located is None:
        return {"rejected": False, "reason": f"Model not found: {model_id}"}

    artifact = located["artifact"]
    metadata = artifact["metadata"]
    if metadata["lifecycle_status"] == "promoted":
        return {"rejected": False, "reason": "Promoted models must be deactivated first."}

    rejected_metadata = {
        **metadata,
        "lifecycle_status": "rejected",
        "rejected_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    rejected_path = lifecycle_artifact_path(rejected_metadata["model_id"], "rejected", model_dir)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": artifact["pipeline"], "metadata": rejected_metadata}, rejected_path)
    _remove_path(Path(located["path"]))
    return {
        "rejected": True,
        "model_id": rejected_metadata["model_id"],
        "specialist": rejected_metadata["specialist"],
        "path": str(rejected_path),
        "metadata": rejected_metadata,
    }


def find_specialist_model(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    root = Path(model_dir or DEFAULT_SPECIALIST_MODEL_DIR)
    if not root.exists():
        return None
    normalized = _safe_name(model_id)
    for path in sorted(root.glob("*/*.joblib")):
        if path.stem != normalized and path.parent.name != "promoted":
            continue
        artifact = _load_artifact_path(path)
        if artifact is None:
            continue
        if artifact["metadata"].get("model_id") == model_id:
            return {"path": str(path), "artifact": artifact}
    return None


def _load_artifact_path(path: Path) -> dict[str, Any] | None:
    try:
        artifact = joblib.load(path)
    except Exception:
        return None
    if not isinstance(artifact, dict) or "pipeline" not in artifact:
        return None
    metadata = artifact.get("metadata")
    if not validate_model_metadata(metadata, require_promoted=False):
        return None
    return artifact


def _path_for_metadata(metadata: dict[str, Any], model_dir: str | Path | None = None) -> Path:
    if metadata["lifecycle_status"] == "promoted":
        return specialist_artifact_path(metadata["specialist"], model_dir)
    return lifecycle_artifact_path(metadata["model_id"], metadata["lifecycle_status"], model_dir)


def _normalize_lifecycle_status(status: Any) -> str:
    normalized = str(status or "candidate").strip().lower().replace("-", "_")
    if normalized not in MODEL_LIFECYCLE_STATUSES:
        raise ValueError(f"Unknown model lifecycle status: {status}")
    return normalized


def _new_model_id(specialist: str) -> str:
    return f"{_safe_name(specialist)}-{uuid4().hex[:12]}"


def _remove_path(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remove_specialist_model_dir(model_dir: str | Path) -> None:
    shutil.rmtree(model_dir, ignore_errors=True)
