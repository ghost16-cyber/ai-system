from __future__ import annotations

from typing import Any

from backend.app.project_analysis.impact import analyze_impact
from backend.app.project_analysis.models import confidence_level


def build_analysis_plan(index: dict[str, Any], requirement: str, *, relevant_paths: list[str] | None = None) -> dict[str, Any]:
    impact = analyze_impact(index, requirement, relevant_paths=relevant_paths)
    files = [item for item in index.get("files", []) if item.get("parse_status") != "excluded"]
    complete = sum(1 for item in files if item.get("parse_status") == "complete")
    partial = sum(1 for item in files if item.get("parse_status") == "partial")
    failed = sum(1 for item in files if item.get("parse_status") == "failed")
    lexical = sum(1 for item in files if item.get("parser") == "lexical_fallback")
    score = 0.45 + (0.30 * complete / max(1, len(files))) - (0.18 * failed / max(1, len(files))) - (0.14 * lexical / max(1, len(files)))
    if impact["coherent_file_set"]:
        score += 0.14
    if impact["limit_exceeded"]:
        score = min(score, 0.35)
    level = confidence_level(max(0.0, min(score, 0.95)))
    uncertainties = []
    if partial:
        uncertainties.append(f"{partial} file(s) use partial structural parsing.")
    if failed:
        uncertainties.append(f"{failed} file(s) contain bounded parse failures.")
    if lexical:
        uncertainties.append("Lexical JavaScript/TypeScript fallback lowers relationship certainty.")
    if impact["limit_exceeded"]:
        uncertainties.append(str(impact["plan_only_reason"]))
    symbols = []
    coherent_paths = {item["relative_path"] for item in impact["coherent_file_set"]}
    for item in files:
        if item.get("relative_path") in coherent_paths:
            symbols.extend({"relative_path": item["relative_path"], "name": symbol.get("name"), "kind": symbol.get("kind"), "range": symbol.get("range")} for symbol in item.get("symbols", [])[:20])
    relationships = [item for item in index.get("relationships", []) if item.get("source_path") in coherent_paths or item.get("target_path") in coherent_paths][:80]
    return {
        "analysis_id": index["analysis_id"], "index_version": index["index_version"],
        "status": "completed", "structural_findings": _findings(files),
        "relevant_symbols": symbols[:80], "dependency_relationships": relationships,
        "impact": impact, "coherent_file_set": impact["coherent_file_set"],
        "impacted_tests": [item["relative_path"] for item in impact["coherent_file_set"] if "test" in item["relative_path"].lower()],
        "syntax_validation_plan": ["Parse every changed Python, JavaScript, TypeScript, JSON, and YAML file virtually before preview."],
        "runtime_validation_plan": ["Propose only the narrowest existing allowlisted test or build command after patch application."],
        "confidence": {"level": level, "basis": "parser coverage, relationship resolution, requirement evidence, impacted tests, and bounded prevalidation", "warnings": uncertainties},
        "uncertainties": uncertainties,
        "plan_only": level == "low" or impact["limit_exceeded"],
        "plan_only_reasons": uncertainties if level == "low" or impact["limit_exceeded"] else [],
        "prevalidation": {"status": "not_started", "checks": [], "warnings": []},
    }


def _findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for item in files[:80]:
        kinds = sorted({str(symbol.get("kind")) for symbol in item.get("symbols", []) if symbol.get("kind")})
        details = []
        if kinds:
            details.append(", ".join(kinds[:8]))
        if item.get("routes"):
            details.append(f"{len(item['routes'])} route(s)")
        if item.get("tests") or "test" in str(item.get("relative_path")).lower():
            details.append("test coverage")
        if item.get("scripts"):
            details.append("manifest scripts")
        if details:
            findings.append({"relative_path": item["relative_path"], "summary": "; ".join(details), "parse_status": item.get("parse_status")})
    return findings[:60]


__all__ = ["build_analysis_plan"]
