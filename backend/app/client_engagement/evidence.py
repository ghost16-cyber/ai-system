from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.app.client_engagement.contracts import (
    ENGAGEMENT_SCHEMA_VERSION,
    EngagementEvidenceReference,
    EvidenceSourceType,
    Sensitivity,
)
from backend.app.client_engagement.limits import EngagementLimits, STAGE10_LIMITS


_SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "secrets", "token"}
_TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml"}


def collect_authorized_evidence(
    *,
    engagement_id: str,
    conversation_id: str,
    original_request: str,
    folder_root: str | Path | None = None,
    folder_access_id: str | None = None,
    clarification_answers: Iterable[dict[str, Any]] = (),
    structural_summary: dict[str, Any] | None = None,
    project_metadata: dict[str, Any] | None = None,
    uploaded_documents: Iterable[dict[str, Any]] = (),
    user_constraints: Iterable[str] = (),
    limits: EngagementLimits = STAGE10_LIMITS,
    now: datetime | None = None,
) -> list[EngagementEvidenceReference]:
    """Collect bounded evidence only from caller-supplied authorized contexts.

    The function deliberately accepts no user-provided arbitrary evidence path. A folder
    root is usable only together with its completed folder-access identifier.
    """
    collected_at = now or datetime.now(timezone.utc)
    result: list[EngagementEvidenceReference] = []
    result.append(_evidence(
        engagement_id, EvidenceSourceType.ORIGINAL_REQUEST, f"conversation:{conversation_id}:request",
        excerpt=_bounded(original_request, limits.max_evidence_excerpt_chars), content=original_request,
        authorization=f"conversation:{conversation_id}", sensitivity=Sensitivity.INTERNAL,
        collected_at=collected_at, limits=limits,
    ))
    for answer in sorted(clarification_answers, key=lambda item: (str(item.get("created_at") or ""), str(item.get("answer_id") or ""))):
        answer_id = str(answer.get("answer_id") or "")
        text = str(answer.get("answer") or "").strip()
        if answer_id and text:
            result.append(_evidence(
                engagement_id, EvidenceSourceType.CLARIFICATION, answer_id,
                excerpt=_bounded(text, limits.max_evidence_excerpt_chars), content=text,
                authorization=f"conversation:{conversation_id}", sensitivity=Sensitivity.INTERNAL,
                collected_at=collected_at, limits=limits,
            ))
    for offset, constraint in enumerate(user_constraints):
        text = str(constraint).strip()
        if text:
            result.append(_evidence(
                engagement_id, EvidenceSourceType.USER_CONSTRAINT, f"constraint:{offset + 1}",
                excerpt=_bounded(text, limits.max_evidence_excerpt_chars), content=text,
                authorization=f"conversation:{conversation_id}", sensitivity=Sensitivity.INTERNAL,
                collected_at=collected_at, limits=limits,
            ))
    for document in sorted(uploaded_documents, key=lambda item: str(item.get("document_id") or item.get("sha256") or "")):
        identifier = str(document.get("document_id") or document.get("sha256") or "")
        if not identifier or str(document.get("conversation_id") or conversation_id) != conversation_id:
            continue
        summary = _safe_metadata(document)
        result.append(_evidence(
            engagement_id, EvidenceSourceType.UPLOADED_DOCUMENT, identifier,
            summary=summary, content=str(summary), authorization=f"conversation:{conversation_id}:upload",
            sensitivity=Sensitivity.INTERNAL, collected_at=collected_at, limits=limits,
        ))
    if project_metadata:
        summary = _safe_metadata(project_metadata)
        result.append(_evidence(
            engagement_id, EvidenceSourceType.PROJECT_METADATA, f"engagement:{engagement_id}:project-metadata",
            summary=summary, content=str(summary), authorization=f"conversation:{conversation_id}",
            sensitivity=Sensitivity.INTERNAL, collected_at=collected_at, limits=limits,
        ))
    if structural_summary:
        summary = _safe_structural_summary(structural_summary)
        result.append(_evidence(
            engagement_id, EvidenceSourceType.STRUCTURAL_SUMMARY, str(structural_summary.get("analysis_id") or "stage6-summary"),
            summary=summary, content=str(summary), authorization=f"conversation:{conversation_id}:stage6",
            sensitivity=Sensitivity.INTERNAL, collected_at=collected_at, limits=limits,
        ))
    if folder_root is not None:
        if not folder_access_id:
            raise ValueError("An authorized folder-access identifier is required for folder evidence.")
        result.extend(_folder_metadata(
            engagement_id=engagement_id, root=folder_root, access_id=folder_access_id,
            collected_at=collected_at, limits=limits,
        ))
    ordered = sorted(result, key=_evidence_order)
    if len(ordered) > limits.max_evidence_items:
        ordered = ordered[:limits.max_evidence_items]
    return ordered


def _folder_metadata(*, engagement_id: str, root: str | Path, access_id: str,
                     collected_at: datetime, limits: EngagementLimits) -> list[EngagementEvidenceReference]:
    approved = Path(root).resolve(strict=True)
    values: list[EngagementEvidenceReference] = []
    for candidate in sorted(approved.rglob("*"), key=lambda item: item.as_posix().lower()):
        if len(values) >= limits.max_evidence_items:
            break
        try:
            if candidate.is_symlink():
                resolved = candidate.resolve(strict=True)
                if not _within(resolved, approved):
                    continue
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=True)
            if not _within(resolved, approved):
                continue
            relative = candidate.relative_to(approved).as_posix()
            stat = candidate.stat()
        except (OSError, ValueError):
            continue
        sensitive = _is_sensitive(relative)
        summary: dict[str, Any] = {
            "relative_path": relative,
            "size_bytes": int(stat.st_size),
            "extension": candidate.suffix.lower(),
            "modified_ns": int(stat.st_mtime_ns),
            "content_included": False,
        }
        # Folder evidence is metadata-only. Even apparently safe text is never returned in cards.
        content_hash = _file_hash(candidate) if stat.st_size <= 10 * 1024 * 1024 and not sensitive else None
        values.append(_evidence(
            engagement_id, EvidenceSourceType.AUTHORIZED_FOLDER, relative,
            summary=summary, content=f"{relative}:{stat.st_size}:{stat.st_mtime_ns}",
            authorization=f"folder-access:{access_id}",
            sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.INTERNAL,
            collected_at=collected_at, limits=limits, content_hash=content_hash,
        ))
    return values


def _evidence(engagement_id: str, source_type: EvidenceSourceType, identifier: str, *,
              collected_at: datetime, authorization: str, sensitivity: Sensitivity,
              limits: EngagementLimits, excerpt: str | None = None,
              summary: dict[str, Any] | None = None, content: str = "",
              content_hash: str | None = None) -> EngagementEvidenceReference:
    stable = f"{engagement_id}\0{source_type.value}\0{identifier}"
    evidence_id = "ev-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]
    digest = content_hash or hashlib.sha256(content.encode("utf-8")).hexdigest()
    return EngagementEvidenceReference(
        schema_version=ENGAGEMENT_SCHEMA_VERSION, evidence_id=evidence_id,
        engagement_id=engagement_id, source_type=source_type,
        source_identifier=_bounded(identifier, 500), excerpt=_bounded(excerpt or "", limits.max_evidence_excerpt_chars) or None,
        structured_summary=summary or {}, content_hash=digest, collected_at=collected_at,
        authorization_context=_bounded(authorization, 500),
        stale_after=collected_at + limits.staleness_threshold,
        sensitivity=sensitivity, is_stale=False,
    )


def _evidence_order(item: EngagementEvidenceReference) -> tuple[int, str, str]:
    priority = {
        EvidenceSourceType.ORIGINAL_REQUEST: 0,
        EvidenceSourceType.CLARIFICATION: 1,
        EvidenceSourceType.USER_CONSTRAINT: 2,
        EvidenceSourceType.UPLOADED_DOCUMENT: 3,
        EvidenceSourceType.PROJECT_METADATA: 4,
        EvidenceSourceType.STRUCTURAL_SUMMARY: 5,
        EvidenceSourceType.AUTHORIZED_FOLDER: 6,
    }
    return (priority[item.source_type], item.source_identifier.lower(), item.evidence_id)


def public_evidence(item: EngagementEvidenceReference) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "source_type": item.source_type.value,
        "label": _public_label(item),
        "content_hash": item.content_hash,
        "is_stale": item.is_stale,
        "sensitivity": item.sensitivity.value,
    }


def _public_label(item: EngagementEvidenceReference) -> str:
    if item.source_type == EvidenceSourceType.AUTHORIZED_FOLDER:
        return str(item.structured_summary.get("relative_path") or "Authorized project item")
    if item.source_type == EvidenceSourceType.ORIGINAL_REQUEST:
        return "Original request"
    return item.source_type.value.replace("_", " ").title()


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {"document_id", "filename", "size_bytes", "sha256", "media_type", "title", "kind", "language", "framework"}
    return {key: value[key] for key in sorted(allowed & value.keys()) if isinstance(value[key], (str, int, float, bool, type(None)))}


def _safe_structural_summary(value: dict[str, Any]) -> dict[str, Any]:
    files = value.get("files") or value.get("inventory") or []
    return {
        "analysis_id": str(value.get("analysis_id") or ""),
        "analysis_hash": str(value.get("analysis_hash") or value.get("project_hash") or ""),
        "file_count": len(files) if isinstance(files, list) else int(value.get("file_count") or 0),
        "languages": sorted(str(item) for item in (value.get("languages") or []) if item)[:20],
    }


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_sensitive(relative: str) -> bool:
    lowered = relative.lower()
    return any(part in lowered for part in _SENSITIVE_NAMES)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(128 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded(value: str, limit: int) -> str:
    text = str(value).replace("\x00", "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


__all__ = ["collect_authorized_evidence", "public_evidence"]
