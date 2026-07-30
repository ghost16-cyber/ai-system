from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .dataset_loader import load_jsonl_dataset


DEFAULT_DATASET_REGISTRY_PATH = Path("data/specialists/dataset_registry.json")
DATASET_STATUSES = {"uploaded", "validated", "approved", "rejected", "archived"}


def register_dataset(
    dataset_path: str | Path,
    *,
    dataset_id: str | None = None,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    loaded = load_jsonl_dataset(dataset_path)
    now = _utc_now()
    rows = loaded["rows"]
    expected_labels = Counter(row["expected_label"] for row in rows)
    specialists = Counter(row["specialist"] for row in rows)
    status = "validated" if rows and not loaded["errors"] and not loaded["missing"] else "uploaded"
    record = {
        "dataset_id": dataset_id or f"specialist-dataset-{uuid4().hex[:12]}",
        "path": str(dataset_path),
        "status": status,
        "created_at": now,
        "updated_at": now,
        "sample_count": len(rows),
        "label_counts": dict(sorted(expected_labels.items())),
        "specialist_counts": dict(sorted(specialists.items())),
        "validation_errors": loaded["errors"],
        "missing": loaded["missing"],
        "approved_at": None,
        "archived_at": None,
        "rejected_at": None,
    }
    registry = _load_registry(registry_path)
    registry[record["dataset_id"]] = record
    _save_registry(registry, registry_path)
    return {"registered": True, "dataset": record}


def list_datasets(registry_path: str | Path | None = None) -> dict[str, Any]:
    registry = _load_registry(registry_path)
    return {
        "path": str(Path(registry_path or DEFAULT_DATASET_REGISTRY_PATH)),
        "datasets": list(sorted(registry.values(), key=lambda item: item["created_at"])),
    }


def get_dataset(
    dataset_id: str,
    registry_path: str | Path | None = None,
) -> dict[str, Any] | None:
    return _load_registry(registry_path).get(dataset_id)


def approve_dataset(
    dataset_id: str,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    registry = _load_registry(registry_path)
    record = registry.get(dataset_id)
    if record is None:
        return {"approved": False, "reason": f"Dataset not found: {dataset_id}"}
    if record["status"] == "archived":
        return {"approved": False, "reason": "Archived datasets cannot be approved."}
    if record["missing"] or record["validation_errors"] or record["sample_count"] <= 0:
        record = _set_dataset_status(record, "rejected")
        registry[dataset_id] = record
        _save_registry(registry, registry_path)
        return {"approved": False, "reason": "Dataset failed validation.", "dataset": record}

    record = _set_dataset_status(record, "approved")
    record["approved_at"] = record["updated_at"]
    registry[dataset_id] = record
    _save_registry(registry, registry_path)
    return {"approved": True, "dataset": record}


def archive_dataset(
    dataset_id: str,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    registry = _load_registry(registry_path)
    record = registry.get(dataset_id)
    if record is None:
        return {"archived": False, "reason": f"Dataset not found: {dataset_id}"}
    record = _set_dataset_status(record, "archived")
    record["archived_at"] = record["updated_at"]
    registry[dataset_id] = record
    _save_registry(registry, registry_path)
    return {"archived": True, "dataset": record}


def _set_dataset_status(record: dict[str, Any], status: str) -> dict[str, Any]:
    if status not in DATASET_STATUSES:
        raise ValueError(f"Unknown dataset status: {status}")
    updated = {**record, "status": status, "updated_at": _utc_now()}
    if status == "rejected":
        updated["rejected_at"] = updated["updated_at"]
    return updated


def _load_registry(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    registry_path = Path(path or DEFAULT_DATASET_REGISTRY_PATH)
    if not registry_path.exists():
        return {}
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(dataset_id): record
        for dataset_id, record in raw.items()
        if isinstance(record, dict)
    }


def _save_registry(
    registry: dict[str, dict[str, Any]],
    path: str | Path | None = None,
) -> None:
    registry_path = Path(path or DEFAULT_DATASET_REGISTRY_PATH)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
