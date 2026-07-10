from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.assignments.schemas import AssignmentManifestWriteResult, AssignmentProjectManifest


MANIFEST_NAME = "assignment_manifest.json"
SECRET_KEY_PARTS = ("password", "secret", "token", "credential", "api_key", "apikey")


def build_assignment_manifest(
    copilot_result: dict[str, Any],
    *,
    assignment_number: int,
    dataset_path: str | None = None,
    document_path: str | None = None,
    last_updated: datetime | None = None,
) -> AssignmentProjectManifest:
    sanitized = _sanitize(copilot_result)
    blueprints = sanitized.get("code_blueprints") if isinstance(sanitized, dict) else []
    generated_files = [
        str(blueprint.get("file_path"))
        for blueprint_set in blueprints if isinstance(blueprint_set, dict)
        for blueprint in blueprint_set.get("blueprints", [])
        if isinstance(blueprint, dict) and blueprint.get("file_path")
    ]
    evidence = _dict(sanitized.get("evidence_checklist"))
    task_breakdown = _dict(sanitized.get("task_breakdown"))
    report_skeleton = _dict(sanitized.get("report_skeleton"))
    readiness = _dict(sanitized.get("final_readiness"))
    runbooks = sanitized.get("runbooks") if isinstance(sanitized, dict) else []
    runbook_steps = [
        _sanitize(step)
        for runbook in runbooks if isinstance(runbook, dict)
        for step in runbook.get("steps", [])
        if isinstance(step, dict)
    ]
    return AssignmentProjectManifest(
        assignment_number=assignment_number,
        dataset_path=dataset_path,
        document_path=document_path or str(_dict(sanitized.get("parsed_document_summary")).get("source_path") or ""),
        generated_files=sorted(dict.fromkeys(generated_files)),
        report_files=[
            "report_package/report_outline.md",
            "report_package/evidence_checklist.md",
            "report_package/runbook.md",
            "report_package/marking_readiness.md",
            "report_package/appendix_code_checklist.md",
        ],
        evidence_checklist={
            "title": evidence.get("title"),
            "summary": evidence.get("summary", {}),
            "required_count": len(evidence.get("required_items", [])) if isinstance(evidence.get("required_items"), list) else 0,
            "optional_count": len(evidence.get("optional_items", [])) if isinstance(evidence.get("optional_items"), list) else 0,
            "items": [
                {
                    "evidence_id": item.get("evidence_id"),
                    "assignment_name": item.get("assignment_name"),
                    "title": item.get("title"),
                    "evidence_type": item.get("evidence_type"),
                    "status": item.get("status"),
                    "priority": item.get("priority"),
                    "marks": item.get("marks"),
                }
                for item in evidence.get("items", [])
                if isinstance(item, dict)
            ],
        },
        task_breakdown={
            "title": task_breakdown.get("title"),
            "tasks": task_breakdown.get("tasks", []),
        },
        report_skeleton={
            "title": report_skeleton.get("title"),
            "sections": [
                {
                    "section_id": section.get("section_id"),
                    "title": section.get("title"),
                    "needs_user_evidence": section.get("needs_user_evidence"),
                }
                for section in report_skeleton.get("sections", [])
                if isinstance(section, dict)
            ],
        },
        runbook_steps=runbook_steps,
        safe_commands=_sanitize(sanitized.get("safe_next_commands", [])),
        missing_screenshots=list(readiness.get("missing_screenshots", [])),
        missing_report_sections=list(readiness.get("missing_report_sections", [])),
        readiness_level=str(readiness.get("readiness_level") or "not_started"),
        last_updated=last_updated or datetime.now(UTC),
        tools_executed=False,
        files_written=False,
        credentials_included=False,
    )


def write_assignment_manifest(
    workspace_root: str | Path,
    manifest: AssignmentProjectManifest,
    *,
    overwrite: bool = False,
) -> AssignmentManifestWriteResult:
    root = normalize_path_for_platform(workspace_root).path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / MANIFEST_NAME).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return AssignmentManifestWriteResult(
            workspace_path=str(root),
            manifest_path=str(target),
            written=False,
            refused=True,
            warnings=["Manifest path must stay inside the approved workspace."],
            overwrite=overwrite,
        )
    if target.exists() and not overwrite:
        return AssignmentManifestWriteResult(
            workspace_path=str(root),
            manifest_path=str(target),
            written=False,
            skipped=True,
            warnings=["assignment_manifest.json already exists; pass overwrite=true to replace it."],
            overwrite=overwrite,
        )
    payload = _sanitize(manifest.model_dump(mode="json"))
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return AssignmentManifestWriteResult(
        workspace_path=str(root),
        manifest_path=str(target),
        written=True,
        overwrite=overwrite,
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key in {"credentials_included", "credentials_written"}:
                sanitized[str(key)] = _sanitize(item)
            elif any(part in lowered_key for part in SECRET_KEY_PARTS):
                sanitized[str(key)] = "<redacted>"
            else:
                sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value
