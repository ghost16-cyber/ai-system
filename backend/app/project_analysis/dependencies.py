from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from backend.app.project_analysis.models import MAX_RELATIONSHIPS


def build_dependency_graph(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = {str(item["relative_path"]) for item in files}
    relationships: list[dict[str, Any]] = []
    definitions: dict[str, list[str]] = {}
    for item in files:
        path = str(item["relative_path"])
        for symbol in item.get("symbols", []):
            definitions.setdefault(str(symbol.get("name", "")).split(".")[-1], []).append(path)
    for item in files:
        source = str(item["relative_path"])
        language = str(item.get("language") or "")
        for imported in item.get("imports", []):
            module = str(imported.get("module") or "")
            target = _resolve_import(source, module, language, paths, int(imported.get("level") or 0))
            relationships.append(_relationship(source, target, "import", "high" if target else "medium", imported.get("range"), unresolved=module if not target else None))
            for name in imported.get("names", [])[:40]:
                relationships.append(_relationship(source, target, "imported_name", "high" if target else "medium", imported.get("range"), symbol=str(name), unresolved=module if not target else None))
        for call in item.get("calls", []):
            name = str(call.get("name") or "").split(".")[-1]
            candidates = [path for path in definitions.get(name, []) if path != source]
            relationships.append(_relationship(source, candidates[0] if len(candidates) == 1 else None, "call", str(call.get("confidence") or "medium"), call.get("range"), symbol=name, unresolved=name if len(candidates) != 1 else None))
        for route in item.get("routes", []):
            relationships.append(_relationship(source, source, "route_handler", "high", route.get("range"), symbol=str(route.get("handler") or ""), detail=str(route.get("path") or "")))
        if _is_test(source):
            target = _test_target(source, paths)
            relationships.append(_relationship(source, target, "tests", "high" if target else "medium", None, unresolved=None if target else "convention target"))
        for symbol in item.get("symbols", []):
            for base in symbol.get("bases", []) if isinstance(symbol, dict) else []:
                candidates = definitions.get(str(base).split(".")[-1], [])
                relationships.append(_relationship(source, candidates[0] if len(candidates) == 1 else None, "inherits", "high" if len(candidates) == 1 else "medium", symbol.get("range"), symbol=str(base), unresolved=str(base) if len(candidates) != 1 else None))
        if len(relationships) >= MAX_RELATIONSHIPS:
            break
    return relationships[:MAX_RELATIONSHIPS]


def _relationship(source: str, target: str | None, kind: str, confidence: str, line_range: Any, *, symbol: str | None = None, unresolved: str | None = None, detail: str | None = None) -> dict[str, Any]:
    return {"source_path": source, "target_path": target, "relationship_type": kind, "confidence": confidence, "range": line_range, "symbol": symbol, "unresolved_target": unresolved, "detail": detail}


def _resolve_import(source: str, module: str, language: str, paths: set[str], level: int) -> str | None:
    if language == "python":
        base = PurePosixPath(source).parent
        if level:
            for _ in range(max(0, level - 1)):
                base = base.parent
        candidate = (base / module.replace(".", "/")).as_posix() if level else module.replace(".", "/")
        for value in (f"{candidate}.py", f"{candidate}/__init__.py"):
            if value in paths:
                return value
    elif module.startswith("."):
        base = PurePosixPath(source).parent
        candidate = (base / module).as_posix()
        candidate = str(PurePosixPath(candidate))
        for value in (candidate, *[candidate + suffix for suffix in (".ts", ".tsx", ".js", ".jsx", ".json")], *[candidate + "/index" + suffix for suffix in (".ts", ".tsx", ".js", ".jsx")]):
            if value in paths:
                return value
    return None


def _is_test(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")) or "/tests/" in f"/{path}"


def _test_target(path: str, paths: set[str]) -> str | None:
    source = PurePosixPath(path)
    name = source.name
    stem = name[5:-3] if name.startswith("test_") and name.endswith(".py") else name.split(".test.")[0].split(".spec.")[0]
    for candidate in sorted(paths):
        item = PurePosixPath(candidate)
        if candidate != path and item.stem == stem and not _is_test(candidate):
            return candidate
    return None


__all__ = ["build_dependency_graph"]
