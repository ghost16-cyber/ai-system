from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from backend.app.assignments.schemas import (
    AssignmentReadinessSummaryV2,
    AssignmentVerificationSnapshot,
    ManualEvidenceReview,
    RequirementVerification,
    WorkspaceEvidenceItem,
)


VERIFICATION_SCHEMA_VERSION = 1
MAX_INVENTORY_FILES = 5_000
NEAR_EMPTY_BYTES = 20
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CONFIG_NAMES = {"pyproject.toml", "pytest.ini", "tox.ini", "requirements.txt", ".env.example"}
COMPOSE_NAMES = {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}
SUPPORTED_EXTENSIONS = {
    ".py", ".md", ".csv", ".tsv", ".json", ".ipynb", ".pdf", ".docx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".yml", ".yaml", ".toml",
    ".ini", ".txt", ".html",
}
SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"
)
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


class AssignmentVerificationError(ValueError):
    pass


def build_workspace_evidence_inventory(
    project_root: str | Path,
    workspace: str | Path,
) -> list[WorkspaceEvidenceItem]:
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Assignment workspace")
    if not workdir.is_dir():
        raise AssignmentVerificationError("Assignment workspace must exist and be a directory.")
    inventory: list[WorkspaceEvidenceItem] = []
    for path in sorted(workdir.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_symlink():
            raise AssignmentVerificationError("Assignment evidence inventory rejects symlinks.")
        resolved = path.resolve()
        _require_inside(resolved, workdir, "Assignment evidence path")
        if path.is_dir() and path.name.lower() in {"output", "outputs", "generated", "generated_outputs"}:
            relative = path.relative_to(workdir).as_posix()
            stat = path.stat()
            inventory.append(WorkspaceEvidenceItem(
                evidence_reference=f"directory:{relative}",
                relative_path=relative,
                file_type="generated_output_directory",
                size=0,
                sha256=hashlib.sha256(b"").hexdigest(),
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                evidence_category="generated_output",
                warnings=["Directory presence does not verify the contents it may contain."],
            ))
            continue
        if not path.is_file():
            continue
        if len(inventory) >= MAX_INVENTORY_FILES:
            raise AssignmentVerificationError("Assignment evidence inventory file limit exceeded.")
        relative = path.relative_to(workdir).as_posix()
        stat = path.stat()
        warnings: list[str] = []
        if stat.st_size == 0:
            warnings.append("File is empty.")
        elif stat.st_size <= NEAR_EMPTY_BYTES:
            warnings.append("File is near-empty and may not contain meaningful evidence.")
        file_type, category = _classify_file(relative, path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS and path.name not in CONFIG_NAMES:
            warnings.append("Unsupported evidence type; manual review is required.")
        inventory.append(WorkspaceEvidenceItem(
            evidence_reference=f"file:{relative}",
            relative_path=relative,
            file_type=file_type,
            size=stat.st_size,
            sha256=_sha256(path),
            modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
            evidence_category=category,
            warnings=warnings,
        ))
    return inventory


def verify_assignment_workspace(
    *,
    metadata_root: str | Path,
    command_store_root: str | Path,
    project_root: str | Path,
    assignment_id: str,
    workspace: str | Path,
    assignment_output: dict[str, Any],
) -> AssignmentVerificationSnapshot:
    assignment_key = _validate_assignment_id(assignment_id)
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Assignment workspace")
    workspace_relative = workdir.relative_to(root).as_posix()
    inventory = build_workspace_evidence_inventory(root, workdir)
    command_records = _load_command_records(command_store_root, assignment_key, workdir)
    decisions = _load_reviews(metadata_root, assignment_key, workspace_relative)
    requirement_inputs = _extract_requirements(assignment_output)
    requirements = [
        _verify_requirement(item, inventory, command_records, decisions)
        for item in requirement_inputs
    ]
    verified_at = datetime.now(timezone.utc)
    readiness = _build_readiness(assignment_key, verified_at, requirements)
    snapshot = AssignmentVerificationSnapshot(
        schema_version=VERIFICATION_SCHEMA_VERSION,
        snapshot_id=f"verification-{uuid4().hex}",
        assignment_id=assignment_key,
        workspace=workspace_relative,
        verification_timestamp=verified_at,
        inventory=inventory,
        requirements=requirements,
        readiness=readiness,
        manual_reviews=decisions,
        warnings=["Verification is deterministic evidence-readiness analysis, not an academic completion decision."],
    )
    _write_json_atomic(_snapshot_path(metadata_root, assignment_key, workspace_relative), snapshot.model_dump(mode="json"))
    return snapshot


def load_verification_snapshot(
    metadata_root: str | Path,
    assignment_id: str,
    workspace_relative: str,
) -> AssignmentVerificationSnapshot:
    path = _snapshot_path(metadata_root, _validate_assignment_id(assignment_id), workspace_relative)
    if not path.is_file():
        raise FileNotFoundError("Assignment verification snapshot not found; run verification first.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AssignmentVerificationSnapshot.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise AssignmentVerificationError("Assignment verification snapshot is corrupt or unsupported.") from error


def record_manual_evidence_review(
    *,
    metadata_root: str | Path,
    assignment_id: str,
    workspace_relative: str,
    requirement_id: str,
    evidence_reference: str,
    decision: str,
    note: str,
) -> ManualEvidenceReview:
    snapshot = load_verification_snapshot(metadata_root, assignment_id, workspace_relative)
    requirement = next((item for item in snapshot.requirements if item.requirement_id == requirement_id), None)
    if requirement is None:
        raise AssignmentVerificationError("Requirement does not belong to this assignment verification snapshot.")
    valid_references = {f"file:{path}" for path in requirement.linked_workspace_files} | set(requirement.linked_execution_evidence)
    if evidence_reference not in valid_references:
        raise AssignmentVerificationError("Evidence reference is not linked to this assignment requirement.")
    if decision not in {"accepted", "rejected", "needs_replacement"}:
        raise AssignmentVerificationError("Manual evidence decision is invalid.")
    review = ManualEvidenceReview(
        requirement_id=requirement_id,
        evidence_reference=evidence_reference,
        decision=decision,
        note=_redact(note.strip()),
        timestamp=datetime.now(timezone.utc),
    )
    reviews = _load_reviews(metadata_root, assignment_id, workspace_relative)
    reviews.append(review)
    _write_json_atomic(
        _reviews_path(metadata_root, assignment_id, workspace_relative),
        {"schema_version": VERIFICATION_SCHEMA_VERSION, "assignment_id": assignment_id, "workspace": workspace_relative, "reviews": [item.model_dump(mode="json") for item in reviews]},
    )
    updated_requirements = []
    for item in snapshot.requirements:
        if item.requirement_id != requirement_id:
            updated_requirements.append(item)
            continue
        status = item.status
        warnings = list(item.warnings)
        if decision == "accepted" and status == "requires_manual_review":
            status = "verified"
        elif decision == "rejected":
            status = "failed"
            warnings.append("A reviewer rejected linked evidence.")
        elif decision == "needs_replacement":
            status = "missing"
            warnings.append("A reviewer marked linked evidence as needing replacement.")
        updated_requirements.append(item.model_copy(update={
            "status": status,
            "warnings": list(dict.fromkeys(warnings)),
            "reviewer_notes": [*item.reviewer_notes, *([review.note] if review.note else [])],
        }))
    reviewed_at = review.timestamp
    snapshot = snapshot.model_copy(update={
        "snapshot_id": f"verification-{uuid4().hex}",
        "verification_timestamp": reviewed_at,
        "requirements": updated_requirements,
        "readiness": _build_readiness(assignment_id, reviewed_at, updated_requirements),
        "manual_reviews": reviews,
    })
    _write_json_atomic(
        _snapshot_path(metadata_root, assignment_id, workspace_relative),
        snapshot.model_dump(mode="json"),
    )
    return review


def _extract_requirements(output: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = output.get("requirements")
    if isinstance(explicit, list):
        candidates = [item for item in explicit if isinstance(item, dict)]
    else:
        evidence = output.get("evidence_checklist")
        evidence_items = evidence.get("items", []) if isinstance(evidence, dict) else []
        candidates = []
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            candidates.append({
                "requirement_id": item.get("evidence_id"),
                "title": item.get("title"),
                "description": item.get("description"),
                "source_reference": item.get("source_requirement"),
                "requirement_category": item.get("evidence_type"),
                "required_deliverable_type": item.get("evidence_type"),
                "expected_evidence": [item.get("suggested_filename")] if item.get("suggested_filename") else [],
                "verification_method": "deterministic_evidence_match",
                "task_id": item.get("task_name"),
                "optional": not bool(item.get("required", True)),
            })
        action_plan = output.get("action_plan")
        checklist = action_plan.get("checklist", []) if isinstance(action_plan, dict) else []
        for item in checklist:
            if not isinstance(item, dict) or not item.get("task_id"):
                continue
            candidates.append({
                "requirement_id": item["task_id"],
                "title": item.get("title"),
                "description": item.get("required_output") or item.get("title"),
                "source_reference": item.get("assignment_name") or "assignment action plan",
                "requirement_category": item.get("group") or "assignment_task",
                "required_deliverable_type": _infer_deliverable(item),
                "expected_evidence": item.get("evidence_needed") or [],
                "verification_method": "deterministic_task_evidence_match",
                "task_id": item["task_id"],
                "optional": bool(item.get("optional")),
            })
    manifest = output.get("manifest") or output.get("project_manifest")
    manifest_files = manifest.get("generated_files", []) if isinstance(manifest, dict) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(candidates, start=1):
        requirement_id = str(item.get("requirement_id") or f"requirement-{index}").strip()
        if not requirement_id or requirement_id in seen:
            continue
        seen.add(requirement_id)
        enriched = {**item, "requirement_id": requirement_id}
        expected = enriched.get("expected_evidence")
        if not expected and manifest_files:
            matching = _matching_manifest_files(enriched, manifest_files)
            if matching:
                enriched["expected_evidence"] = matching
        result.append(enriched)
    if not result:
        raise AssignmentVerificationError("No structured assignment requirements were supplied for verification.")
    return result


def _verify_requirement(
    item: dict[str, Any],
    inventory: list[WorkspaceEvidenceItem],
    commands: list[dict[str, Any]],
    reviews: list[ManualEvidenceReview],
) -> RequirementVerification:
    requirement_id = str(item["requirement_id"])
    title = _redact(str(item.get("title") or requirement_id))
    description = _redact(str(item.get("description") or title))
    category = str(item.get("requirement_category") or "unknown").lower()
    deliverable = str(item.get("required_deliverable_type") or "unknown").lower()
    expected = [str(value) for value in item.get("expected_evidence", []) if value]
    task_id = str(item.get("task_id") or requirement_id)
    optional = bool(item.get("optional"))
    candidates = _candidate_files(title, description, category, deliverable, expected, inventory)
    execution = _candidate_commands(title, description, task_id, category, commands)
    warnings: list[str] = []
    status = "not_started"
    confidence = 0.2

    has_rule = bool(expected) or bool(_wanted_types(category, deliverable))
    if optional and str(item.get("status")) == "not_applicable":
        status, confidence = "not_applicable", 1.0
    elif not has_rule:
        status, confidence = "requires_manual_review", 0.2
        warnings.append("Requirement has no valid deterministic verification rule.")
    elif not candidates and not execution:
        status, confidence = "missing", 0.95
        warnings.append("No matching workspace or controlled execution evidence was found.")
    elif any(any("empty" in warning.lower() for warning in candidate.warnings) for candidate in candidates):
        status, confidence = "failed", 0.95
        warnings.append("A required candidate file is empty or near-empty.")
    elif category in {"screenshot", "dashboard", "terminal_output", "validation_query"} or deliverable in {"screenshot", "image"}:
        status, confidence = "requires_manual_review", 0.9
        warnings.append("Image or captured-output presence is detected; visual correctness requires manual review.")
    elif category in {"report_answer", "appendix", "report"} or deliverable in {"report", "pdf", "docx"}:
        status, confidence = "requires_manual_review", 0.9
        warnings.append("Report presence is detected; content quality requires manual review.")
    elif category in {"test", "tests", "testing"} or "test" in deliverable or any(command.get("action") == "pytest" for command in execution):
        status, confidence, test_warnings = _verify_tests(candidates, execution, inventory)
        warnings.extend(test_warnings)
    elif candidates:
        status, confidence = "detected", 0.75
        warnings.append("File presence does not prove functional or academic correctness.")
        if any("unsupported evidence type" in warning.lower() for candidate in candidates for warning in candidate.warnings):
            status = "requires_manual_review"
    elif execution:
        if any(command.get("status") in {"failed", "timed_out"} for command in execution):
            status, confidence = "failed", 0.95
        else:
            status, confidence = "partially_verified", 0.7
            warnings.append("Execution evidence alone does not verify the full requirement.")
    else:
        status, confidence = "requires_manual_review", 0.3
        warnings.append("Requirement has no valid deterministic verification rule.")

    linked_files = [candidate.relative_path for candidate in candidates]
    linked_commands = [f"assignment-command:{command['plan_id']}" for command in execution]
    review_history = [review for review in reviews if review.requirement_id == requirement_id and review.evidence_reference in ({f"file:{path}" for path in linked_files} | set(linked_commands))]
    latest_by_reference: dict[str, ManualEvidenceReview] = {}
    for review in sorted(review_history, key=lambda value: value.timestamp):
        latest_by_reference[review.evidence_reference] = review
    applicable_reviews = list(latest_by_reference.values())
    decisions = {review.decision for review in applicable_reviews}
    if "accepted" in decisions and "rejected" in decisions:
        status = "requires_manual_review"
        warnings.append("Conflicting manual evidence decisions are recorded.")
    elif "rejected" in decisions:
        status = "failed"
        warnings.append("A reviewer rejected linked evidence.")
    elif "needs_replacement" in decisions:
        status = "missing"
        warnings.append("A reviewer marked linked evidence as needing replacement.")
    elif "accepted" in decisions and status == "requires_manual_review":
        status, confidence = "verified", 1.0

    return RequirementVerification(
        requirement_id=requirement_id,
        title=title,
        description=description,
        source_reference=_redact(str(item.get("source_reference") or "assignment workflow")),
        requirement_category=category,
        required_deliverable_type=deliverable,
        expected_evidence=[_redact(value) for value in expected],
        verification_method=str(item.get("verification_method") or "deterministic_evidence_match"),
        status=status,
        confidence=confidence,
        warnings=list(dict.fromkeys(warnings)),
        linked_workspace_files=linked_files,
        linked_execution_evidence=linked_commands,
        reviewer_notes=[review.note for review in applicable_reviews if review.note],
    )


def _verify_tests(candidates, commands, inventory):
    if not candidates and not commands:
        return "missing", 0.95, ["Expected test evidence is missing."]
    passed = [command for command in commands if command.get("action") == "pytest" and command.get("status") == "succeeded" and command.get("exit_code") == 0]
    failed = [command for command in commands if command.get("action") == "pytest" and command.get("status") in {"failed", "timed_out"}]
    if passed and failed:
        return "requires_manual_review", 0.9, ["Conflicting passing and failed controlled test evidence exists."]
    if failed:
        return "failed", 0.98, ["Controlled test execution failed."]
    if passed:
        newest = max((item.modified_at for item in inventory if item.file_type in {"python", "python_test"}), default=None)
        finished = max((_parse_datetime(command.get("finished_at")) for command in passed), default=None)
        if newest and finished and newest > finished:
            return "partially_verified", 0.85, ["Controlled test evidence is stale because relevant files changed afterward."]
        return "verified", 0.95, ["Passing controlled pytest evidence supports this testing requirement only."]
    return "detected", 0.7, ["Test file exists but no passing controlled pytest execution is linked."]


def _candidate_files(title, description, category, deliverable, expected, inventory):
    patterns = [value.lower() for value in expected]
    words = {word for word in re.findall(r"[a-z0-9_]+", f"{title} {description}".lower()) if len(word) >= 4}
    wanted_types = _wanted_types(category, deliverable)
    candidates = []
    for item in inventory:
        path = item.relative_path.lower()
        pattern_match = any(Path(pattern).name == Path(path).name or Path(path).match(pattern) for pattern in patterns if pattern)
        type_match = item.file_type in wanted_types
        word_match = bool(words & set(re.findall(r"[a-z0-9_]+", path)))
        if pattern_match or (type_match and (word_match or not patterns)):
            candidates.append(item)
    return candidates


def _candidate_commands(title, description, task_id, category, commands):
    needles = {value.lower() for value in (title, description, task_id) if value}
    result = []
    for command in commands:
        task = str(command.get("assignment_task") or "").lower()
        task_match = any(needle in task or task in needle for needle in needles if needle and task)
        test_match = category in {"test", "tests", "testing"} and command.get("action") == "pytest"
        if task_match or test_match:
            result.append(command)
    return result


def _load_command_records(store_root, assignment_id, workspace):
    records = []
    root = Path(store_root).expanduser().resolve()
    if not root.is_dir():
        return records
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("assignment_id") == assignment_id and value.get("workspace_path") == str(workspace):
            records.append(value)
    return records


def _build_readiness(assignment_id, timestamp, requirements):
    distribution = Counter(item.status for item in requirements)
    covered = sum(1 for item in requirements if item.status not in {"not_started", "missing"})
    freshness = "stale" if any("stale" in warning.lower() for item in requirements for warning in item.warnings) else "current_or_not_applicable"
    blockers = [item.title for item in requirements if item.status in {"missing", "failed"}]
    manual = [item.title for item in requirements if item.status == "requires_manual_review"]
    actions = [f"Provide or replace evidence for: {title}." for title in blockers[:5]]
    actions.extend(f"Manually review linked evidence for: {title}." for title in manual[:5])
    if not actions:
        actions.append("Review verified evidence and retain reviewer notes; readiness is not a guarantee of correctness.")
    return AssignmentReadinessSummaryV2(
        assignment_id=assignment_id,
        verification_timestamp=timestamp,
        total_requirements=len(requirements),
        status_distribution=dict(distribution),
        verified_count=distribution["verified"],
        partially_verified_count=distribution["partially_verified"],
        missing_count=distribution["missing"],
        failed_count=distribution["failed"],
        manual_review_count=distribution["requires_manual_review"],
        blocking_issues=blockers,
        warnings=["Evidence coverage is not an academic completion or submission-readiness decision."],
        evidence_coverage_percentage=round((covered / len(requirements) * 100) if requirements else 0.0, 1),
        execution_evidence_freshness=freshness,
        recommended_next_actions=actions,
        academic_completion_inferred=False,
    )


def _classify_file(relative, path):
    suffix = path.suffix.lower()
    name = path.name.lower()
    parts = {part.lower() for part in Path(relative).parts}
    if parts & {"output", "outputs", "generated", "generated_outputs"}: return "generated_output", "generated_output"
    if suffix == ".py" and (name.startswith("test_") or "tests" in parts): return "python_test", "test"
    if suffix == ".py": return "python", "implementation"
    if name in COMPOSE_NAMES: return "docker_compose", "configuration"
    if suffix in IMAGE_EXTENSIONS: return "image", "screenshot"
    if suffix in {".csv", ".tsv"}: return "dataset", "dataset"
    if suffix == ".json": return "json", "structured_data"
    if suffix == ".ipynb": return "notebook", "implementation"
    if suffix == ".pdf": return "pdf", "report"
    if suffix == ".docx": return "docx", "report"
    if suffix == ".md": return "markdown", "report" if "report" in relative.lower() else "documentation"
    if name in CONFIG_NAMES or suffix in {".yml", ".yaml", ".toml", ".ini"}: return "configuration", "configuration"
    if "output" in parts or "outputs" in parts or "generated" in parts: return "generated_output", "generated_output"
    return "unsupported", "other"


def _wanted_types(category, deliverable):
    text = f"{category} {deliverable}"
    if any(value in text for value in ("screenshot", "dashboard", "image", "terminal_output", "validation_query")): return {"image"}
    if any(value in text for value in ("report", "appendix", "pdf", "docx")): return {"markdown", "pdf", "docx"}
    if "dataset" in text or "csv" in text or "tsv" in text: return {"dataset"}
    if "test" in text: return {"python_test"}
    if "notebook" in text: return {"notebook"}
    if "compose" in text or "config" in text: return {"docker_compose", "configuration"}
    if "code" in text or "python" in text or "implementation" in text: return {"python", "notebook"}
    return set()


def _infer_deliverable(item):
    if item.get("screenshot_needed"): return "screenshot"
    if item.get("report_section_needed"): return "report"
    return "implementation"


def _matching_manifest_files(item, manifest_files):
    text = f"{item.get('title', '')} {item.get('description', '')}".lower()
    words = {word for word in re.findall(r"[a-z0-9_]+", text) if len(word) >= 4}
    deliverable = str(item.get("required_deliverable_type") or "").lower()
    matches = []
    for value in manifest_files:
        path = str(value)
        path_words = set(re.findall(r"[a-z0-9_]+", path.lower()))
        suffix = Path(path).suffix.lower()
        type_match = (
            ("python" in deliverable or "code" in deliverable) and suffix == ".py"
        ) or ("report" in deliverable and suffix in {".md", ".pdf", ".docx"})
        if words & path_words or type_match:
            matches.append(path)
    return sorted(dict.fromkeys(matches))


def _load_reviews(metadata_root, assignment_id, workspace_relative):
    path = _reviews_path(metadata_root, assignment_id, workspace_relative)
    if not path.is_file(): return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != VERIFICATION_SCHEMA_VERSION or value.get("assignment_id") != assignment_id or value.get("workspace") != workspace_relative:
            raise AssignmentVerificationError("Manual evidence review record is corrupt or belongs to another assignment.")
        return [ManualEvidenceReview.model_validate(item) for item in value.get("reviews", [])]
    except (OSError, json.JSONDecodeError, ValidationError, AttributeError) as error:
        raise AssignmentVerificationError("Manual evidence review record is corrupt or unsupported.") from error


def _snapshot_path(metadata_root, assignment_id, workspace_relative):
    return Path(metadata_root).expanduser().resolve() / "snapshots" / f"{_record_key(assignment_id, workspace_relative)}.json"


def _reviews_path(metadata_root, assignment_id, workspace_relative):
    return Path(metadata_root).expanduser().resolve() / "reviews" / f"{_record_key(assignment_id, workspace_relative)}.json"


def _record_key(assignment_id, workspace_relative):
    digest = hashlib.sha256(f"{assignment_id}|{workspace_relative}".encode()).hexdigest()[:24]
    return f"{assignment_id}-{digest}"


def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_datetime(value):
    if not value: return None
    try: return datetime.fromisoformat(value)
    except (TypeError, ValueError): return None


def _redact(value):
    redacted = SECRET_RE.sub(r"\1\2<redacted>", value)
    return OPENAI_KEY_RE.sub("<redacted-api-key>", redacted)


def _validate_assignment_id(value):
    assignment_id = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", assignment_id):
        raise AssignmentVerificationError("Assignment identifier is invalid.")
    return assignment_id


def _require_inside(path, root, label):
    try: path.relative_to(root)
    except ValueError as error: raise AssignmentVerificationError(f"{label} must stay inside the configured workspace root.") from error
