from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.app.project_analysis.models import MAX_IMPACT_FILES


def analyze_impact(index: dict[str, Any], requirement: str, *, relevant_paths: list[str] | None = None, max_files: int = MAX_IMPACT_FILES, max_bytes: int = 500_000) -> dict[str, Any]:
    tokens = {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", requirement) if token.lower() not in _STOP}
    requested = set(relevant_paths or [])
    scores: dict[str, tuple[int, list[str]]] = {}
    files = {str(item.get("relative_path")): item for item in index.get("files", [])}
    for path, item in files.items():
        score = 1 if path in requested else 0
        reasons = ["available as bounded requirement evidence"] if path in requested else []
        searchable = " ".join([path, *(str(symbol.get("name") or "") for symbol in item.get("symbols", [])), *(str(route.get("path") or "") for route in item.get("routes", []))]).lower()
        hits = sorted(token for token in tokens if token in searchable)
        if hits:
            score += min(6, len(hits) * 2)
            reasons.append(f"matches requested concepts: {', '.join(hits[:4])}")
        if _is_test(path) and any(token in requirement.lower() for token in ("test", "acceptance", "implement", "fix", "add")):
            score += 2
            reasons.append("existing validation coverage")
        scores[path] = (score, reasons)
    for relationship in index.get("relationships", []):
        source, target = relationship.get("source_path"), relationship.get("target_path")
        if source in scores and target in scores and (scores[source][0] > 0 or scores[target][0] > 0):
            for path, other in ((source, target), (target, source)):
                score, reasons = scores[path]
                if score < 5:
                    scores[path] = (score + 2, [*reasons, f"{relationship.get('relationship_type')} relationship with {other}"])
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1][0], pair[0]))
    selected = [(path, value) for path, value in ranked if value[0] > 0]
    total_bytes = sum(int(files[path].get("size_bytes") or 0) for path, _ in selected[:max_files])
    exceeded = len(selected) > max_files or total_bytes > max_bytes
    entries = []
    for path, (score, reasons) in ranked:
        classification = "required" if score >= 6 else "likely_required" if score >= 3 else "optional" if score > 0 else "unrelated"
        entries.append({"relative_path": path, "classification": classification, "reason": "; ".join(reasons)[:400] or "No static relationship to the requested change.", "symbols": [symbol.get("name") for symbol in files[path].get("symbols", [])[:12]]})
    coherent = [item for item in entries if item["classification"] in {"required", "likely_required"}][:max_files]
    return {"files": entries[:80], "coherent_file_set": coherent, "limit_exceeded": exceeded, "total_selected_bytes": total_bytes, "max_files": max_files, "max_bytes": max_bytes, "plan_only_reason": "The coherent change set exceeds bounded file or byte limits; narrow the task." if exceeded else None}


def _is_test(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith("test_") or ".test." in name or ".spec." in name or "/tests/" in f"/{path}"


_STOP = {"this", "that", "with", "from", "into", "project", "review", "implement", "feature", "described", "readme", "preserve", "existing", "behavior", "while", "when", "without", "source", "unrelated", "audit_label", "unchanged", "remain", "module"}


__all__ = ["analyze_impact"]
