from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from backend.app.folders.reader import ReadLimits, read_project_file
from backend.app.folders.safety import safe_relative_path
from backend.app.project_analysis.model_synthesis.contracts import EvidenceExcerpt, EvidencePackage
from backend.app.project_analysis.models import ProjectAnalysisError
from backend.app.project_control.contracts import content_hash


MAX_EVIDENCE_FILES = 8
MAX_EXCERPTS_PER_FILE = 2
MAX_EXCERPT_LINES = 120
MAX_EXCERPT_CHARS = 6_000
MAX_EVIDENCE_CHARS = 24_000
_SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".md", ".toml"}


def build_evidence_package(root: str | Path, job: dict[str, Any]) -> EvidencePackage:
    index = dict(job.get("analysis_index") or {})
    analysis = dict(job.get("analysis") or {})
    if not index or analysis.get("plan_only"):
        raise ProjectAnalysisError("Model-assisted synthesis requires a current non-plan-only Stage 6 analysis.")
    coherent = [safe_relative_path(str(item.get("relative_path") or "")) for item in analysis.get("coherent_file_set", [])]
    coherent = list(dict.fromkeys(coherent))[:MAX_EVIDENCE_FILES]
    known = {str(item.get("relative_path")): item for item in index.get("files", [])}
    coherent = [path for path in coherent if path in known and known[path].get("parse_status") != "excluded"]
    if not coherent:
        raise ProjectAnalysisError("Stage 6 did not identify a coherent bounded file set for model-assisted synthesis.")
    task = str(job.get("user_task") or "")
    delete_paths = [path for path in coherent if re.search(rf"\b(?:delete|remove)\b[^\n]{{0,80}}`?{re.escape(path)}`?", task, re.I)]
    create_paths = _explicit_missing_paths(task, set(known))
    excerpts: list[EvidenceExcerpt] = []
    used_chars = 0
    for path in coherent:
        record = read_project_file(root, path, limits=ReadLimits(max_bytes_per_file=250_000))
        if record.get("status") != "readable":
            raise ProjectAnalysisError(f"Model synthesis evidence is no longer readable: {path}")
        text = str(record.get("text") or "")
        lines = text.splitlines(keepends=True)
        ranges = _evidence_ranges(known[path], len(lines))
        for start, end in ranges[:MAX_EXCERPTS_PER_FILE]:
            content = "".join(lines[start - 1:end])[:MAX_EXCERPT_CHARS]
            if not content or used_chars + len(content) > MAX_EVIDENCE_CHARS:
                continue
            excerpts.append(EvidenceExcerpt(path=path, start_line=start, end_line=end, sha256=str(known[path].get("file_hash") or ""), content=content))
            used_chars += len(content)
    if not excerpts:
        raise ProjectAnalysisError("No bounded readable excerpts were available for model-assisted synthesis.")
    allowed = set(coherent)
    relationships = [
        item for item in index.get("relationships", [])
        if item.get("source_path") in allowed or item.get("target_path") in allowed
    ][:80]
    impact_files = list((analysis.get("impact") or {}).get("files") or [])
    excluded = [str(item.get("relative_path")) for item in impact_files if item.get("classification") == "unrelated"][:40]
    excluded.extend(str(item.get("relative_path")) for item in index.get("files", []) if item.get("parse_status") == "excluded")
    manifests = [path for path in known if Path(path).name.lower() in {"pyproject.toml", "package.json", "requirements.txt", "pytest.ini", "tsconfig.json"}][:12]
    config_excerpts: list[EvidenceExcerpt] = []
    for path in manifests:
        if path in {item.path for item in excerpts}:
            continue
        record = read_project_file(root, path, limits=ReadLimits(max_bytes_per_file=64_000))
        text = str(record.get("text") or "") if record.get("status") == "readable" else ""
        content = text[: min(MAX_EXCERPT_CHARS, max(0, MAX_EVIDENCE_CHARS - used_chars))]
        if content:
            lines = content.splitlines()
            config_excerpts.append(EvidenceExcerpt(
                path=path, start_line=1, end_line=max(1, len(lines)),
                sha256=str(known[path].get("file_hash") or ""), content=content,
            ))
            used_chars += len(content)
    specification = dict(job.get("specification") or {})
    plan = dict(job.get("plan") or {})
    criteria = list(specification.get("acceptance_criteria") or plan.get("acceptance_criteria") or [])[:50]
    approved_requirements = [
        str(value)[:1000] for value in (
            specification.get("in_scope_requirements") or job.get("requirements") or ()
        ) if isinstance(value, str) and value
    ][:50]
    criterion_hashes = {
        str(item.get("criterion_id") or item.get("id") or index): content_hash(item)
        for index, item in enumerate(criteria) if isinstance(item, dict)
    }
    missing_evidence: list[str] = []
    excerpt_paths = {item.path for item in excerpts}
    for path in coherent:
        if path not in excerpt_paths:
            missing_evidence.append(f"No bounded source excerpt was available for {path}.")
    for path in list(analysis.get("impacted_tests") or []):
        if path not in known:
            missing_evidence.append(f"Impacted test was not present in the current index: {path}.")
    return EvidencePackage(
        package_version="astra.project-evidence.v1", analysis_id=str(index["analysis_id"]),
        index_version=str(index["index_version"]), root_fingerprint=str(index["root_fingerprint"]),
        objective=str(job.get("objective") or job.get("user_task") or "")[:2000],
        allowed_modify_paths=sorted(coherent), allowed_create_paths=sorted(create_paths),
        allowed_delete_paths=sorted(delete_paths), excluded_paths=sorted(set(excluded))[:40],
        excerpts=excerpts, symbols=list(analysis.get("relevant_symbols") or [])[:80],
        relationships=relationships, impacted_tests=list(analysis.get("impacted_tests") or [])[:20],
        manifests=manifests, uncertainties=list(analysis.get("uncertainties") or [])[:20],
        limits={"max_files": MAX_EVIDENCE_FILES, "max_excerpts_per_file": MAX_EXCERPTS_PER_FILE,
                "max_excerpt_lines": MAX_EXCERPT_LINES, "max_excerpt_chars": MAX_EXCERPT_CHARS,
                "max_global_chars": MAX_EVIDENCE_CHARS},
        approved_requirements=approved_requirements, criterion_hashes=criterion_hashes,
        plan_revision_id=str(job.get("plan_revision_id") or "") or None,
        scope_revision_id=str(job.get("scope_revision_id") or "") or None,
        work_unit_id=str(job.get("work_unit_id") or job.get("active_work_unit_id") or "") or None,
        manifest_hash=str(job.get("manifest_hash") or job.get("project_state_hash") or "") or None,
        config_excerpts=config_excerpts, missing_evidence=missing_evidence[:30],
        byte_accounting={
            "source_excerpt_bytes": sum(len(item.content.encode("utf-8")) for item in excerpts),
            "config_excerpt_bytes": sum(len(item.content.encode("utf-8")) for item in config_excerpts),
            "total_excerpt_bytes": sum(len(item.content.encode("utf-8")) for item in (*excerpts, *config_excerpts)),
            "maximum_bytes": MAX_EVIDENCE_CHARS,
        },
    )


def evidence_summary(evidence: EvidencePackage) -> dict[str, Any]:
    return {
        "file_count": len(set(item.path for item in evidence.excerpts)),
        "excerpt_count": len(evidence.excerpts),
        "excerpt_chars": sum(len(item.content) for item in evidence.excerpts),
        "allowed_modify_count": len(evidence.allowed_modify_paths),
        "allowed_create_count": len(evidence.allowed_create_paths),
        "allowed_delete_count": len(evidence.allowed_delete_paths),
        "relationship_count": len(evidence.relationships),
        "symbol_count": len(evidence.symbols),
        "missing_evidence_count": len(evidence.missing_evidence),
        "total_excerpt_bytes": evidence.byte_accounting.get("total_excerpt_bytes", 0),
        "project_rag_enabled": evidence.project_rag_enabled,
    }


def evidence_hash(evidence: EvidencePackage) -> str:
    return hashlib.sha256(evidence.model_dump_json(exclude_none=False).encode("utf-8")).hexdigest()


def _evidence_ranges(item: dict[str, Any], line_count: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for symbol in item.get("symbols", [])[:12]:
        source = symbol.get("range") or {}
        start = max(1, int(source.get("start_line") or 1) - 3)
        end = min(line_count, max(start, int(source.get("end_line") or start) + 3), start + MAX_EXCERPT_LINES - 1)
        candidate = (start, end)
        if not any(start <= old_end and end >= old_start for old_start, old_end in ranges):
            ranges.append(candidate)
        if len(ranges) >= MAX_EXCERPTS_PER_FILE:
            break
    if not ranges:
        ranges.append((1, min(line_count or 1, MAX_EXCERPT_LINES)))
    return ranges


def _explicit_missing_paths(task: str, known: set[str]) -> list[str]:
    values: list[str] = []
    for raw in re.findall(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|js|jsx|ts|tsx|json|ya?ml|md|toml)\b", task):
        try:
            path = safe_relative_path(raw.replace("\\", "/"))
        except Exception:
            continue
        if path not in known and Path(path).suffix.lower() in _SOURCE_SUFFIXES and path not in values:
            values.append(path)
    return values[:4]


__all__ = ["build_evidence_package", "evidence_hash", "evidence_summary"]
