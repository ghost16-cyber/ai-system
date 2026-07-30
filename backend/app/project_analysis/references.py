from __future__ import annotations

from typing import Any


def search_references(index: dict[str, Any], query: str, *, limit: int = 30) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    results: list[dict[str, Any]] = []
    for item in index.get("files", []):
        path = str(item.get("relative_path") or "")
        for symbol in item.get("symbols", []):
            name = str(symbol.get("name") or "")
            if needle in name.lower() or needle in path.lower():
                results.append({"relative_path": path, "symbol": name, "relationship": "definition", "range": symbol.get("range"), "confidence": "high" if item.get("parse_status") == "complete" else "medium"})
        for route in item.get("routes", []):
            if needle in str(route.get("path") or "").lower() or needle in str(route.get("handler") or "").lower():
                results.append({"relative_path": path, "symbol": route.get("handler"), "relationship": "route", "range": route.get("range"), "confidence": "high"})
    for relationship in index.get("relationships", []):
        if needle in " ".join(str(relationship.get(key) or "") for key in ("source_path", "target_path", "symbol", "detail", "unresolved_target")).lower():
            results.append({"relative_path": relationship.get("source_path"), "symbol": relationship.get("symbol"), "relationship": relationship.get("relationship_type"), "range": relationship.get("range"), "confidence": relationship.get("confidence")})
    unique: list[dict[str, Any]] = []
    seen = set()
    for item in results:
        key = (item.get("relative_path"), item.get("symbol"), item.get("relationship"), str(item.get("range")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:limit]


__all__ = ["search_references"]
