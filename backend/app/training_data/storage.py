from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.training_data.schemas import (
    TrainingExample,
    TrainingExampleLabelRequest,
    TrainingExportFormat,
)

DATASET_RELATIVE_PATH = Path("data/training/intent_examples.jsonl")
EXPORTS_RELATIVE_DIR = Path("data/training/exports")


def dataset_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root).expanduser().resolve() / DATASET_RELATIVE_PATH


def append_example(
    workspace_root: str | Path,
    example: TrainingExample,
    *,
    dedupe_chat_run_id: bool = True,
) -> dict[str, Any]:
    path = dataset_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if dedupe_chat_run_id and example.chat_run_id:
        existing = get_example_by_chat_run_id(workspace_root, example.chat_run_id)
        if existing is not None:
            return {"saved": False, "duplicate": True, "example": existing.model_dump(mode="json")}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(example.model_dump(mode="json"), sort_keys=True) + "\n")
    return {"saved": True, "duplicate": False, "example": example.model_dump(mode="json")}


def list_examples(
    workspace_root: str | Path,
    *,
    label_status: str | None = None,
    final_label: str | None = None,
    source: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    examples = _read_examples(workspace_root)
    filtered = [
        item
        for item in examples
        if (label_status is None or item.label_status == label_status)
        and (final_label is None or item.final_label == final_label)
        and (source is None or item.source == source)
    ]
    filtered = sorted(filtered, key=lambda item: item.created_at, reverse=True)
    capped = filtered[: max(0, min(limit, 200))]
    return {
        "items": [item.model_dump(mode="json") for item in capped],
        "count": len(capped),
        "total_matching": len(filtered),
        "storage_path": str(dataset_path(workspace_root)),
    }


def get_dataset_status(workspace_root: str | Path) -> dict[str, Any]:
    examples = _read_examples(workspace_root)
    labeled = [
        item
        for item in examples
        if item.label_status in {"confirmed", "corrected"} and item.final_label
    ]
    label_distribution = Counter(item.final_label for item in labeled if item.final_label)
    status_distribution = Counter(item.label_status for item in examples)
    path = dataset_path(workspace_root)
    return {
        "total_examples": len(examples),
        "labeled_count": len(labeled),
        "unlabeled_count": len(examples) - len(labeled),
        "label_distribution": dict(sorted(label_distribution.items())),
        "label_status_distribution": dict(sorted(status_distribution.items())),
        "storage_path": str(path),
        "last_updated": _last_updated(path),
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }


def update_example_label(
    workspace_root: str | Path,
    example_id: str,
    request: TrainingExampleLabelRequest,
) -> dict[str, Any]:
    examples = _read_examples(workspace_root)
    updated: TrainingExample | None = None
    rows: list[TrainingExample] = []
    for item in examples:
        if item.id != example_id:
            rows.append(item)
            continue
        next_item = _apply_label_update(item, request)
        rows.append(next_item)
        updated = next_item
    if updated is None:
        raise LookupError(f"Training example not found: {example_id}")
    _write_examples(workspace_root, rows)
    return {"updated": True, "example": updated.model_dump(mode="json")}


def export_examples(
    workspace_root: str | Path,
    *,
    export_format: TrainingExportFormat,
) -> dict[str, Any]:
    rows = [
        item
        for item in _read_examples(workspace_root)
        if item.label_status in {"confirmed", "corrected"} and item.final_label
    ]
    export_dir = Path(workspace_root).expanduser().resolve() / EXPORTS_RELATIVE_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = export_dir / f"intent_examples_{timestamp}.{export_format}"
    if export_format == "csv":
        _write_csv(path, rows)
    else:
        _write_jsonl(path, rows)
    distribution = Counter(item.final_label for item in rows if item.final_label)
    return {
        "path": str(path),
        "row_count": len(rows),
        "label_distribution": dict(sorted(distribution.items())),
        "format": export_format,
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }


def get_example_by_chat_run_id(
    workspace_root: str | Path,
    chat_run_id: str,
) -> TrainingExample | None:
    return next(
        (item for item in _read_examples(workspace_root) if item.chat_run_id == chat_run_id),
        None,
    )


def _read_examples(workspace_root: str | Path) -> list[TrainingExample]:
    path = dataset_path(workspace_root)
    if not path.exists():
        return []
    examples: list[TrainingExample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            examples.append(TrainingExample.model_validate(raw))
        except (json.JSONDecodeError, ValueError):
            continue
    return examples


def _write_examples(workspace_root: str | Path, examples: list[TrainingExample]) -> None:
    path = dataset_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(path, examples)


def _write_jsonl(path: Path, rows: list[TrainingExample]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[TrainingExample]) -> None:
    fields = [
        "id",
        "created_at",
        "source",
        "chat_run_id",
        "user_message",
        "assistant_response",
        "routed_task_type",
        "routed_specialist",
        "routing_confidence",
        "suggested_label",
        "corrected_label",
        "final_label",
        "label_status",
        "usefulness_rating",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            data = item.model_dump(mode="json")
            writer.writerow({field: data.get(field) for field in fields})


def _apply_label_update(
    item: TrainingExample,
    request: TrainingExampleLabelRequest,
) -> TrainingExample:
    final_label = item.final_label
    corrected_label = request.corrected_label
    if request.label_status == "confirmed":
        final_label = item.suggested_label or corrected_label
    elif request.label_status == "corrected":
        if corrected_label is None:
            raise ValueError("Corrected examples require corrected_label.")
        final_label = corrected_label
    elif request.label_status == "rejected":
        final_label = None
    elif request.label_status == "suggested":
        final_label = None
    elif request.label_status == "unlabeled":
        final_label = None
        corrected_label = None
    return item.model_copy(
        update={
            "updated_at": datetime.now(UTC),
            "corrected_label": corrected_label,
            "final_label": final_label,
            "label_status": request.label_status,
            "usefulness_rating": request.usefulness_rating,
            "notes": _clean_notes(request.notes),
        }
    )


def _last_updated(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")


def _clean_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    cleaned = " ".join(notes.split())
    return cleaned[:500] if cleaned else None
