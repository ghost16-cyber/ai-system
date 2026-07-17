from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path
from typing import Any

from backend.app.folders.reader import ReadLimits, read_project_file
from backend.app.folders.safety import safe_relative_path
from backend.app.project_analysis.inventory import file_hashes
from backend.app.project_analysis.models import ProjectAnalysisError
from backend.app.project_analysis.validation import prevalidate_virtual_files


CONTRACT_FIELDS = {
    "job_id", "conversation_id", "folder_access_id", "root_fingerprint", "analysis_id",
    "index_version", "source_hashes", "requested_operation", "operations",
    "expected_relationships", "expected_tests", "confidence", "warnings",
}
OPERATION_FIELDS = {"operation", "relative_path", "language", "reason", "affected_symbols", "content", "expected_relationships", "expected_tests", "confidence", "warnings"}


def synthesize_project_patch(root: str | Path, job: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(job.get("analysis") or {})
    index = dict(job.get("analysis_index") or {})
    if not index or analysis.get("plan_only"):
        reason = "; ".join(analysis.get("plan_only_reasons") or []) or "Structural confidence is too low for safe synthesis."
        raise ProjectAnalysisError(f"Stage 6 stopped at plan-only mode: {reason}")
    task = f"{job.get('user_task', '')} {_markdown_requirement(root, index)}"
    operations = _rename_python_symbol(root, index, task)
    if not operations and "category" in task.lower() and "filter" in task.lower():
        operations = _optional_category_filter(root, index)
    if not operations:
        raise ProjectAnalysisError("No coherent bounded multi-file synthesis pattern matched the structural evidence; the evidence-backed plan remains available.")
    contract = {
        "job_id": job["job_id"], "conversation_id": job["conversation_id"], "folder_access_id": job["folder_access_id"],
        "root_fingerprint": job["root_fingerprint"], "analysis_id": index["analysis_id"], "index_version": index["index_version"],
        "source_hashes": {path: file_hashes(index).get(path, "missing") for path in [item["relative_path"] for item in operations]},
        "requested_operation": "coherent_multi_file_update", "operations": operations,
        "expected_relationships": ["route delegates to service with optional category", "tests cover filtered and unfiltered behavior"],
        "expected_tests": [item["relative_path"] for item in operations if "test" in item["relative_path"].lower()],
        "confidence": analysis.get("confidence", {}).get("level", "medium"), "warnings": analysis.get("uncertainties", []),
    }
    validate_contract(contract)
    prevalidation = prevalidate_virtual_files(root, index, contract)
    changes = [{"path": item["relative_path"], "operation": item["operation"], "content": item.get("content", ""), "explanation": item["reason"]} for item in operations]
    return {"contract": contract, "changes": changes, "prevalidation": prevalidation, "analysis_context": {"analysis_id": index["analysis_id"], "index_version": index["index_version"], "source_hashes": contract["source_hashes"], "confidence": contract["confidence"], "prevalidation": prevalidation}}


def validate_contract(contract: dict[str, Any]) -> None:
    unknown = set(contract) - CONTRACT_FIELDS
    if unknown:
        raise ProjectAnalysisError(f"Unknown synthesis contract fields: {', '.join(sorted(unknown))}")
    for field in CONTRACT_FIELDS - {"warnings"}:
        if field not in contract:
            raise ProjectAnalysisError(f"Missing synthesis contract field: {field}")
    if not isinstance(contract.get("operations"), list):
        raise ProjectAnalysisError("Synthesis operations must be a list.")
    paths = set()
    for operation in contract["operations"]:
        if not isinstance(operation, dict) or set(operation) - OPERATION_FIELDS:
            raise ProjectAnalysisError("Malformed or unknown synthesis operation fields.")
        path = safe_relative_path(str(operation.get("relative_path") or ""))
        if path in paths:
            raise ProjectAnalysisError("Conflicting duplicate synthesis operation.")
        paths.add(path)
        if str(operation.get("operation")) not in {"create", "modify", "delete"}:
            raise ProjectAnalysisError("Unsupported synthesis operation.")


def _optional_category_filter(root: str | Path, index: dict[str, Any]) -> list[dict[str, Any]]:
    sources = []
    tests = []
    for item in index.get("files", []):
        path = str(item.get("relative_path") or "")
        names = {str(symbol.get("name") or "").split(".")[-1] for symbol in item.get("symbols", [])}
        if path.endswith(".py") and "test" not in Path(path).name.lower():
            sources.append(item)
        if path.endswith(".py") and (Path(path).name.startswith("test_") or "/tests/" in f"/{path}"):
            tests.append(item)
    service = next((item for item in sources if any(str(symbol.get("name")).split(".")[-1] == "list_items" for symbol in item.get("symbols", [])) and not item.get("routes")), None)
    route = next((item for item in sources if item is not service and item.get("routes") and any(str(call.get("name") or "").split(".")[-1] == "list_items" for call in item.get("calls", []))), None)
    test = next((item for item in tests if any(str(call.get("name") or "").split(".")[-1] in {"list_items", "get_items"} for call in item.get("calls", []))), tests[0] if tests else None)
    if not service or not route or not test:
        return []
    service_text = _read(root, service["relative_path"])
    route_text = _read(root, route["relative_path"])
    test_text = _read(root, test["relative_path"])
    service_after = _extend_service(service_text)
    route_after, handler = _extend_route(route_text)
    test_after = _extend_tests(test_text, handler)
    if service_after == service_text or route_after == route_text or test_after == test_text:
        return []
    return [
        _operation(service, service_after, "Add optional category filtering in the service while preserving the unfiltered return path.", ["list_items"]),
        _operation(route, route_after, "Accept the optional route parameter and pass it explicitly to the service layer.", [handler, "list_items"]),
        _operation(test, test_after, "Cover filtered results and preserved behavior without a category.", ["test_list_items_category_filter"]),
    ]


def _rename_python_symbol(root: str | Path, index: dict[str, Any], task: str) -> list[dict[str, Any]]:
    match = re.search(r"\brename\s+(?:the\s+)?(?:python\s+)?(?:function\s+|class\s+|symbol\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?\s+(?:to|as)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", task, re.I)
    if not match or match.group(1) == match.group(2):
        return []
    old, new = match.group(1), match.group(2)
    candidates = []
    definition_found = False
    for item in index.get("files", []):
        if item.get("language") != "python" or item.get("parse_status") != "complete":
            continue
        defines = any(str(symbol.get("name") or "").split(".")[-1] == old for symbol in item.get("symbols", []))
        references = any(str(call.get("name") or "").split(".")[-1] == old for call in item.get("calls", [])) or any(old in item_import.get("names", []) for item_import in item.get("imports", []))
        if defines or references:
            candidates.append(item)
            definition_found = definition_found or defines
    if not definition_found or not candidates:
        return []
    operations = []
    for item in candidates:
        before = _read(root, item["relative_path"])
        after = _rename_python_tokens(before, old, new)
        if after != before:
            operations.append(_operation(item, after, f"Rename Python symbol {old} to {new} and update its statically identified references.", [old, new]))
    return operations if 1 <= len(operations) <= 10 else []


def _rename_python_tokens(text: str, old: str, new: str) -> str:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError):
        return text
    changed = [token._replace(string=new) if token.type == tokenize.NAME and token.string == old else token for token in tokens]
    return tokenize.untokenize(changed)


def _extend_service(text: str) -> str:
    match = re.search(r"(?m)^(?P<indent>\s*)def\s+list_items\s*\(\s*\)\s*(?:->\s*[^:]+)?\s*:\s*\n(?P<bodyindent>\s+)return\s+(?P<collection>[A-Za-z_][A-Za-z0-9_]*)\s*$", text)
    if not match:
        return text
    indent, body, collection = match.group("indent"), match.group("bodyindent"), match.group("collection")
    replacement = f'{indent}def list_items(category: str | None = None):\n{body}if category is None:\n{body}    return {collection}\n{body}return [item for item in {collection} if item.get("category") == category]'
    return text[:match.start()] + replacement + text[match.end():]


def _extend_route(text: str) -> tuple[str, str]:
    calls = list(re.finditer(r"(?m)^(?P<indent>\s*)def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*(?:->\s*[^:]+)?\s*:\s*\n(?P<bodyindent>\s+)return\s+list_items\s*\(\s*\)\s*$", text))
    if len(calls) != 1:
        return text, "get_items"
    match = calls[0]
    replacement = f'{match.group("indent")}def {match.group("name")}(category: str | None = None):\n{match.group("bodyindent")}return list_items(category=category)'
    return text[:match.start()] + replacement + text[match.end():], match.group("name")


def _extend_tests(text: str, handler: str) -> str:
    if "test_list_items_category_filter" in text:
        return text
    imported = re.search(rf"(?m)^from\s+[^\s]+\s+import\s+.*\b{re.escape(handler)}\b", text) is not None
    if not imported:
        return text
    suffix = "\n" if text.endswith("\n") else "\n\n"
    return text + suffix + f'def test_list_items_category_filter():\n    filtered = {handler}(category="work")\n    assert filtered\n    assert all(item["category"] == "work" for item in filtered)\n    assert len({handler}()) >= len(filtered)\n'


def _operation(item: dict[str, Any], content: str, reason: str, symbols: list[str]) -> dict[str, Any]:
    return {"operation": "modify", "relative_path": item["relative_path"], "language": item["language"], "reason": reason, "affected_symbols": symbols, "content": content, "expected_relationships": [], "expected_tests": [], "confidence": "high", "warnings": []}


def _read(root: str | Path, path: str) -> str:
    record = read_project_file(root, path, limits=ReadLimits(max_bytes_per_file=250_000))
    if record["status"] != "readable":
        raise ProjectAnalysisError(f"Synthesis evidence is no longer readable: {path}")
    return str(record["text"])


def _markdown_requirement(root: str | Path, index: dict[str, Any]) -> str:
    values = []
    for item in index.get("files", []):
        if item.get("language") == "markdown":
            record = read_project_file(root, item["relative_path"])
            if record["status"] == "readable":
                values.append(str(record["text"])[:4000])
    return " ".join(values)


__all__ = ["synthesize_project_patch", "validate_contract"]
