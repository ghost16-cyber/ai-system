from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.project_validation.contracts import (
    ArtifactType,
    DeliverableArtifact,
    DeliverableManifest,
    stable_hash,
)
from backend.app.project_validation.workspace import DEFAULT_EXCLUSIONS

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_REPORT_EXTENSIONS = {".md", ".html", ".pdf", ".docx", ".txt"}


def infer_artifact_type(deliverable: dict[str, Any]) -> ArtifactType:
    text = f"{deliverable.get('title', '')} {deliverable.get('description', '')}".lower()
    if any(word in text for word in ("chart", "plot", "histogram", "image")):
        return ArtifactType.CHART
    if "notebook" in text or "jupyter" in text:
        return ArtifactType.NOTEBOOK
    if "report" in text and "html" in text:
        return ArtifactType.HTML_REPORT
    if "report" in text:
        return ArtifactType.MARKDOWN_REPORT
    if any(word in text for word in ("website", "frontend", "page", "responsive")):
        return ArtifactType.WEBSITE_BUILD
    if any(word in text for word in ("api", "backend", "service")):
        return ArtifactType.BACKEND_SERVICE
    if any(word in text for word in ("test", "regression")):
        return ArtifactType.TESTS
    if any(word in text for word in ("dataset", "csv", "analysis output")):
        return ArtifactType.DATASET_OUTPUT
    if any(word in text for word in ("repair", "fix", "patch")):
        return ArtifactType.REPAIR_PATCH
    if any(word in text for word in ("documentation", "readme", "instructions")):
        return ArtifactType.DOCUMENTATION
    return ArtifactType.SOURCE_CODE


def _candidate_files(root: Path, artifact_type: ArtifactType, *, max_files: int = 5000) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in DEFAULT_EXCLUSIONS)
        current_path = Path(current)
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            files.append(path)
            if len(files) >= max_files:
                break
        if len(files) >= max_files:
            break
    if artifact_type == ArtifactType.CHART:
        return [path for path in files if path.suffix.lower() in _IMAGE_EXTENSIONS]
    if artifact_type == ArtifactType.NOTEBOOK:
        return [path for path in files if path.suffix.lower() == ".ipynb"]
    if artifact_type in {ArtifactType.HTML_REPORT, ArtifactType.WEBSITE_BUILD}:
        return [path for path in files if path.suffix.lower() in {".html", ".tsx", ".jsx", ".vue", ".svelte"}]
    if artifact_type in {ArtifactType.MARKDOWN_REPORT, ArtifactType.DOCUMENTATION}:
        return [path for path in files if path.suffix.lower() in _REPORT_EXTENSIONS]
    if artifact_type == ArtifactType.TESTS:
        return [path for path in files if "test" in path.name.lower() or path.parent.name in {"tests", "test"}]
    if artifact_type == ArtifactType.DATASET_OUTPUT:
        return [path for path in files if path.suffix.lower() in {".csv", ".json", ".parquet", ".xlsx"}]
    if artifact_type in {ArtifactType.SOURCE_CODE, ArtifactType.REPAIR_PATCH, ArtifactType.BACKEND_SERVICE}:
        return [path for path in files if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".cs", ".go", ".rs"}]
    return files


def _expected_artifact_count(deliverable: dict[str, Any], artifact_type: ArtifactType) -> int:
    if artifact_type != ArtifactType.CHART:
        return 1
    text = f"{deliverable.get('title', '')} {deliverable.get('description', '')}".lower()
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
    for word, count in words.items():
        if re.search(rf"\b{word}\b", text):
            return count
    match = re.search(r"\b([1-8])\s+(?:required\s+)?(?:charts?|plots?|images?)\b", text)
    return int(match.group(1)) if match else 1


def _keyword_score(path: Path, deliverable: dict[str, Any]) -> int:
    words = {
        word.strip(".,:;()[]{}")
        for word in f"{deliverable.get('title', '')} {deliverable.get('description', '')}".lower().split()
        if len(word.strip(".,:;()[]{}")) >= 4
    }
    name = path.as_posix().lower().replace("_", " ").replace("-", " ")
    return sum(1 for word in words if word in name)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_deliverable_manifest(
    *, run_id: str, workspace_root: str | Path, deliverables: list[dict[str, Any]],
    artifact_hints: dict[str, str] | None = None, max_scan_files: int = 5000,
) -> DeliverableManifest:
    root = Path(workspace_root).expanduser().resolve(strict=True)
    hints = artifact_hints or {}
    artifacts: list[DeliverableArtifact] = []
    missing: list[str] = []
    used_paths: set[Path] = set()
    for deliverable in deliverables:
        deliverable_id = str(deliverable.get("deliverable_id") or deliverable.get("id") or "").strip()
        title = str(deliverable.get("title") or "Deliverable").strip()
        if not deliverable_id:
            raise ValueError("Every deliverable must have an ID.")
        artifact_type = infer_artifact_type(deliverable)
        expected_count = _expected_artifact_count(deliverable, artifact_type)
        criterion_ids = [
            str(item.get("criterion_id") or item.get("id"))
            for item in deliverable.get("acceptance_criteria", [])
            if item.get("criterion_id") or item.get("id")
        ]
        selected: list[Path] = []
        warning: str | None = None
        hint = hints.get(deliverable_id)
        if hint:
            candidate = (root / hint).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise ValueError("An artifact hint escaped the authorized validation workspace.") from error
            if candidate.is_file() and not candidate.is_symlink():
                selected.append(candidate)
                used_paths.add(candidate)
            else:
                warning = "The expected artifact location was not found."
        candidates = _candidate_files(root, artifact_type, max_files=max_scan_files)
        candidates.sort(key=lambda path: (-_keyword_score(path, deliverable), path.as_posix().casefold()))
        for candidate in candidates:
            if len(selected) >= expected_count:
                break
            if candidate in used_paths:
                continue
            selected.append(candidate)
            used_paths.add(candidate)
        if len(selected) < expected_count:
            missing.append(deliverable_id)
            warning = warning or f"Expected {expected_count} matching artifact(s), but found {len(selected)}."
        for index in range(max(1, expected_count)):
            chosen = selected[index] if index < len(selected) else None
            client_name = title if expected_count == 1 else f"{title} {index + 1}"
            artifacts.append(DeliverableArtifact(
                artifact_id=f"artifact-{uuid4().hex}", deliverable_id=deliverable_id,
                client_name=client_name, artifact_type=artifact_type,
                logical_location=hint or f"Detected {artifact_type.value}",
                relative_path=chosen.relative_to(root).as_posix() if chosen else None,
                exists=chosen is not None, size_bytes=chosen.stat().st_size if chosen else 0,
                content_hash=_hash_file(chosen) if chosen else None,
                associated_criterion_ids=criterion_ids,
                inspection_methods=["file_presence", "content_hash"] if chosen else ["file_presence"],
                human_review_required=artifact_type in {ArtifactType.CHART, ArtifactType.WEBSITE_BUILD},
                warning=warning if chosen is None else None,
            ))
    payload = [artifact.model_dump(mode="json") for artifact in artifacts]
    return DeliverableManifest(
        manifest_id=f"manifest-{uuid4().hex}", run_id=run_id, artifacts=artifacts,
        complete=not missing, missing_deliverable_ids=sorted(set(missing)),
        generated_at=datetime.now(timezone.utc), manifest_hash=stable_hash(payload),
    )


__all__ = ["build_deliverable_manifest", "infer_artifact_type"]
