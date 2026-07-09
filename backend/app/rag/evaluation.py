from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.rag.eval_cases import (
    ASTRA_RAG_EVALUATION_CASES,
    RagEvaluationCase,
    list_evaluation_cases,
)
from backend.app.rag.project_indexer import load_project_index, resolve_project_root

LATEST_EVALUATION_RELATIVE_PATH = Path("data/rag/latest_evaluation.json")


def rag_evaluation_status(workspace_root: str | Path) -> dict[str, Any]:
    root = resolve_project_root(workspace_root)
    index_exists = load_project_index(root) is not None
    return {
        "status": "ready" if index_exists else "index_missing",
        "index_exists": index_exists,
        "evaluation_case_count": len(ASTRA_RAG_EVALUATION_CASES),
        "evaluation_cases": list_evaluation_cases(),
        "latest_evaluation": load_latest_evaluation(root),
        "latest_evaluation_path": str(_latest_evaluation_path(root)),
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }


def evaluate_project_rag(
    workspace_root: str | Path,
    *,
    selected_cases: list[str] | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(workspace_root)
    if load_project_index(root) is None:
        return {
            "status": "index_missing",
            "index_exists": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "total_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
            "path_hit_rate": 0.0,
            "average_top_score": 0.0,
            "average_sources_returned": 0.0,
            "cases": [],
            "message": "Project RAG evaluation requires an existing project index.",
            "advisory_only": True,
            "tools_executed": False,
            "patches_applied": False,
            "runtime_authorized": False,
        }

    cases = _select_cases(selected_cases)
    details = [_evaluate_case(root, item) for item in cases]
    total_expected_paths = sum(len(item["expected_paths"]) for item in details)
    total_path_hits = sum(
        len(item["expected_paths"]) - len(item["missing_expected_paths"])
        for item in details
    )
    passed_cases = sum(1 for item in details if item["passed"])
    result = {
        "status": "ready",
        "index_exists": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(details),
        "passed_cases": passed_cases,
        "failed_cases": len(details) - passed_cases,
        "path_hit_rate": _round_metric(
            total_path_hits / total_expected_paths if total_expected_paths else 0.0
        ),
        "average_top_score": _round_metric(
            sum(float(item["score"]) for item in details) / len(details)
            if details
            else 0.0
        ),
        "average_sources_returned": _round_metric(
            sum(int(item["sources_returned"]) for item in details) / len(details)
            if details
            else 0.0
        ),
        "cases": details,
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }
    _persist_latest_evaluation(root, result)
    return result


def load_latest_evaluation(workspace_root: str | Path) -> dict[str, Any] | None:
    path = _latest_evaluation_path(resolve_project_root(workspace_root))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _evaluate_case(root: Path, case: RagEvaluationCase) -> dict[str, Any]:
    from backend.app.rag.context_service import rag_search

    limit = max(10, min(20, len(case.expected_paths) * 4))
    search = rag_search(root, query=case.query, limit=limit)
    raw_results = search.get("results") if isinstance(search, dict) else []
    results = [item for item in raw_results if isinstance(item, dict)]
    returned_paths = _unique_paths(results)
    missing_expected_paths = [
        path for path in case.expected_paths if path not in returned_paths
    ]
    expected_terms_found = _expected_terms_found(results, case.expected_terms)
    score = max((_float(item.get("score")) for item in results), default=0.0)
    return {
        "case_id": case.case_id,
        "query": case.query,
        "category": case.category,
        "description": case.description,
        "expected_paths": list(case.expected_paths),
        "expected_terms": list(case.expected_terms),
        "returned_paths": returned_paths,
        "passed": not missing_expected_paths and expected_terms_found,
        "score": _round_metric(score),
        "missing_expected_paths": missing_expected_paths,
        "expected_terms_found": expected_terms_found,
        "sources_returned": len(results),
    }


def _select_cases(selected_cases: list[str] | None) -> list[RagEvaluationCase]:
    if not selected_cases:
        return list(ASTRA_RAG_EVALUATION_CASES)
    selected = {item.strip() for item in selected_cases if item.strip()}
    return [
        item
        for item in ASTRA_RAG_EVALUATION_CASES
        if item.case_id in selected or item.category in selected
    ]


def _unique_paths(results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for item in results:
        path = str(item.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _expected_terms_found(
    results: list[dict[str, Any]],
    expected_terms: tuple[str, ...],
) -> bool:
    if not expected_terms:
        return True
    haystack = "\n".join(
        " ".join(str(item.get(key) or "") for key in ("path", "title", "snippet"))
        for item in results
    ).lower()
    return any(term.lower() in haystack for term in expected_terms)


def _persist_latest_evaluation(root: Path, result: dict[str, Any]) -> None:
    path = _latest_evaluation_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def _latest_evaluation_path(root: Path) -> Path:
    return root / LATEST_EVALUATION_RELATIVE_PATH


def _round_metric(value: float) -> float:
    return round(float(value), 4)


def _float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
