from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from backend.app.folders.reader import ReadLimits, read_project_file
from backend.app.folders.safety import project_file_exclusion_reason, resolve_project_path, safe_relative_path, validate_root_identity
from backend.app.project_analysis.languages import detect_language
from backend.app.project_analysis.models import MAX_IMPACT_FILES, MAX_SYNTHESIS_BYTES, ProjectAnalysisError
from backend.app.project_analysis.symbols import analyze_source
from backend.app.project_control.contracts import content_hash


def prevalidate_virtual_files(root: str | Path, index: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    approved = validate_root_identity(root, str(contract["root_fingerprint"]))
    if contract.get("analysis_id") != index.get("analysis_id") or contract.get("index_version") != index.get("index_version"):
        raise ProjectAnalysisError("Synthesis is bound to a different or stale structural index.")
    if contract.get("manifest_hash") is not None and contract.get("manifest_hash") != index.get("manifest_hash"):
        raise ProjectAnalysisError("Synthesis is bound to a different project manifest.")
    binding_material = {
        "analysis_id": contract.get("analysis_id"), "index_version": contract.get("index_version"),
        "manifest_hash": contract.get("manifest_hash"), "source_hashes": contract.get("source_hashes"),
        "operations": contract.get("operations"),
    }
    if contract.get("artifact_hash") is not None and contract.get("artifact_hash") != content_hash(binding_material):
        raise ProjectAnalysisError("Synthesis artifact hash does not match its virtual-tree inputs.")
    operations = contract.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > MAX_IMPACT_FILES:
        raise ProjectAnalysisError(f"Synthesis must contain between 1 and {MAX_IMPACT_FILES} bounded file operations.")
    known = {str(item.get("relative_path")): item for item in index.get("files", [])}
    seen: set[str] = set()
    virtual_paths = {path for path, item in known.items() if item.get("parse_status") != "excluded"}
    virtual_hashes = {path: str(item.get("file_hash") or "") for path, item in known.items() if path in virtual_paths}
    virtual: dict[str, str] = {}
    total = 0
    checks = ["root fingerprint", "analysis binding", "operation uniqueness"]
    for operation in operations:
        if not isinstance(operation, dict):
            raise ProjectAnalysisError("Every synthesis operation must be a structured object.")
        path = safe_relative_path(str(operation.get("relative_path") or ""))
        allowed_paths = contract.get("allowed_paths")
        if isinstance(allowed_paths, list) and path not in allowed_paths:
            raise ProjectAnalysisError(f"Synthesis path is outside the exact canonical scope: {path}")
        if path in seen:
            raise ProjectAnalysisError("Synthesis contains conflicting duplicate operations.")
        seen.add(path)
        kind = str(operation.get("operation") or "")
        if kind not in {"create", "modify", "delete"}:
            raise ProjectAnalysisError("Synthesis operations must be create, modify, or delete.")
        existing = known.get(path)
        if kind == "create" and existing:
            raise ProjectAnalysisError(f"Cannot create existing indexed file: {path}")
        if kind in {"modify", "delete"} and not existing:
            raise ProjectAnalysisError(f"Cannot {kind} missing indexed file: {path}")
        target = resolve_project_path(approved, path, must_exist=kind != "create")
        if project_file_exclusion_reason(target, approved, size=0 if kind == "create" else target.stat().st_size):
            raise ProjectAnalysisError(f"Synthesis targets an excluded file: {path}")
        expected = str(contract.get("source_hashes", {}).get(path) or "missing")
        current = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else "missing"
        if expected != current:
            raise ProjectAnalysisError(f"Structural analysis is stale because {path} changed.")
        content = "" if kind == "delete" else str(operation.get("content") if operation.get("content") is not None else "")
        total += len(content.encode("utf-8"))
        if total > MAX_SYNTHESIS_BYTES:
            raise ProjectAnalysisError("Synthesized virtual files exceed the bounded byte limit.")
        _reject_generated_content(content)
        if kind == "delete":
            virtual_paths.discard(path)
            virtual_hashes.pop(path, None)
        else:
            virtual_paths.add(path)
            virtual[path] = content
            virtual_hashes[path] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    checks.extend(["exact source hashes", "path eligibility", "secret scan", "output byte limit"])
    parsed: dict[str, dict[str, Any]] = {}
    for path, content in virtual.items():
        language = detect_language(path)
        result = analyze_source(path, language, content)
        parsed[path] = result
        if result.get("parse_status") == "failed":
            message = str((result.get("syntax_errors") or [{}])[0].get("message") or "syntax error")
            raise ProjectAnalysisError(f"Virtual {language} validation failed for {path}: {message[:180]}")
        if language == "python":
            ast.parse(content)
        if language == "json":
            json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    checks.append("virtual syntax and strict data parsing")
    _validate_changed_imports(approved, virtual_paths, virtual, parsed)
    _validate_deleted_references(index, operations)
    _validate_renamed_symbols(index, parsed, operations)
    _validate_test_and_config_relationships(index, operations, virtual_paths)
    checks.extend([
        "changed imports", "deleted references", "renamed known callers",
        "test and config relationships", "canonical scope", "manifest and artifact binding",
        "full virtual post-change tree",
    ])
    return {
        "status": "passed", "checks": checks, "warnings": _warnings(parsed),
        "validated_files": sorted(seen), "validated_bytes": total,
        "virtual_file_count": len(virtual_paths),
        "virtual_tree_hash": content_hash(sorted(virtual_hashes.items())),
    }


def _reject_generated_content(content: str) -> None:
    lowered = content.lower()
    if any(phrase in lowered for phrase in ("approve patch ", "approve command ", "approve folder ")):
        raise ProjectAnalysisError("Generated content contains a forbidden approval phrase.")
    if re.search(r"\bsk-[A-Za-z0-9_-]{12,}\b", content) or re.search(r"(?im)^\s*[\w.-]*(?:secret|password|api[_-]?key|token)[\w.-]*\s*[:=]\s*['\"]?\S{4,}", content):
        raise ProjectAnalysisError("Generated content appears to contain a secret value.")
    if re.search(r"(?:[A-Za-z]:[\\/](?:Users|Windows|Program Files)|/(?:home|Users|tmp|etc)/|\\\\[^\\\s]+\\)", content):
        raise ProjectAnalysisError("Generated content leaks an absolute filesystem path.")
    if any(placeholder in lowered for placeholder in ("todo: implement", "implementation goes here", "mock result", "sample code only")):
        raise ProjectAnalysisError("Generated implementation contains a forbidden placeholder.")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ProjectAnalysisError(f"Generated JSON contains duplicate key: {key}")
        output[key] = value
    return output


def _validate_changed_imports(root: Path, paths: set[str], virtual: dict[str, str], parsed: dict[str, dict[str, Any]]) -> None:
    for source, result in parsed.items():
        for imported in result.get("imports", []):
            module = str(imported.get("module") or "")
            if not module:
                continue
            if source.endswith(".py") and (int(imported.get("level") or 0) > 0 or module.split(".")[0] in {PurePosixPath(item).stem for item in paths}):
                candidate = module.replace(".", "/")
                if int(imported.get("level") or 0):
                    candidate = (PurePosixPath(source).parent / candidate).as_posix()
                if not any(value in paths for value in (f"{candidate}.py", f"{candidate}/__init__.py")) and module.split(".")[0] not in {PurePosixPath(item).stem for item in paths}:
                    raise ProjectAnalysisError(f"Changed import in {source} has no known local target: {module}")
            if module.startswith(".") and not source.endswith(".py"):
                base = (PurePosixPath(source).parent / module).as_posix()
                if not any(value in paths for value in (base, *[base + suffix for suffix in (".ts", ".tsx", ".js", ".jsx", ".json")])):
                    raise ProjectAnalysisError(f"Changed relative import in {source} has no virtual target: {module}")


def _validate_deleted_references(index: dict[str, Any], operations: list[dict[str, Any]]) -> None:
    deleted = {str(item.get("relative_path")) for item in operations if item.get("operation") == "delete"}
    changed = {str(item.get("relative_path")) for item in operations}
    for relationship in index.get("relationships", []):
        if relationship.get("target_path") in deleted and relationship.get("source_path") not in changed and relationship.get("confidence") == "high":
            raise ProjectAnalysisError(f"Cannot delete {relationship['target_path']}; it is still referenced by {relationship['source_path']}.")


def _validate_renamed_symbols(index: dict[str, Any], parsed: dict[str, dict[str, Any]], operations: list[dict[str, Any]]) -> None:
    changed = {str(item.get("relative_path")) for item in operations}
    original = {str(item.get("relative_path")): {str(symbol.get("name")).split(".")[-1] for symbol in item.get("symbols", [])} for item in index.get("files", [])}
    for path, result in parsed.items():
        removed = original.get(path, set()) - {str(symbol.get("name")).split(".")[-1] for symbol in result.get("symbols", [])}
        for relationship in index.get("relationships", []):
            if relationship.get("target_path") == path and relationship.get("symbol") in removed and relationship.get("source_path") not in changed and relationship.get("confidence") == "high":
                raise ProjectAnalysisError(f"Renamed symbol {relationship['symbol']} still has an unchanged known caller in {relationship['source_path']}.")


def _warnings(parsed: dict[str, dict[str, Any]]) -> list[str]:
    warnings = []
    if any(item.get("parse_status") == "partial" for item in parsed.values()):
        warnings.append("At least one changed file used partial structural parsing.")
    return warnings


def _validate_test_and_config_relationships(
    index: dict[str, Any], operations: list[dict[str, Any]], virtual_paths: set[str]
) -> None:
    changed = {str(item.get("relative_path")) for item in operations}
    deleted = {str(item.get("relative_path")) for item in operations if item.get("operation") == "delete"}
    manifest_names = {"package.json", "pyproject.toml", "requirements.txt", "tsconfig.json", "pytest.ini"}
    for relationship in index.get("relationships", []):
        source = str(relationship.get("source_path") or "")
        target = str(relationship.get("target_path") or "")
        if (source in changed or target in changed) and (source in deleted or target in deleted):
            survivor = target if source in deleted else source
            if survivor and survivor not in virtual_paths:
                raise ProjectAnalysisError("A changed test/config relationship targets a missing virtual file.")
    changed_code = any(Path(path).suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx"} for path in changed)
    if changed_code:
        known_tests = [path for path in virtual_paths if "test" in PurePosixPath(path).name.lower()]
        known_manifests = [path for path in virtual_paths if PurePosixPath(path).name.lower() in manifest_names]
        if not known_tests and not known_manifests:
            raise ProjectAnalysisError("Changed code has no bounded test or toolchain configuration relationship.")


__all__ = ["prevalidate_virtual_files"]
