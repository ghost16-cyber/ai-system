# repo_scanner/planner/target_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ResolvedTarget:
    raw_target: str
    resolved_target: str | None
    confidence: float
    reason: str
    candidates: list[str]


def _to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _normalize_query(target: str) -> str:
    target = target.strip().replace("\\", "/")
    target = target.removesuffix(" module").strip()

    # Convert dotted pseudo-paths:
    # analysis_engine.rules.py -> analysis_engine/rules.py
    if "/" not in target and target.count(".") >= 2 and target.endswith(".py"):
        parts = target.split(".")
        target = "/".join(parts[:-1]) + ".py"

    # Convert module notation:
    # analysis_engine.rules -> analysis_engine/rules.py
    elif "/" not in target and "." in target and not target.endswith(".py"):
        target = target.replace(".", "/") + ".py"

    return target


def _build_repo_index(scan: dict) -> tuple[list[str], list[str]]:
    files = []
    dirs = set()

    for file_info in scan.get("files", []):
        path = file_info.get("path")
        if not isinstance(path, str):
            continue

        normalized = _to_posix(path)
        files.append(normalized)

        p = Path(normalized)
        for parent in p.parents:
            if str(parent) not in {".", ""}:
                dirs.add(_to_posix(parent))

    return files, sorted(dirs)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []

    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)

    return result


def _is_vendor_path(path: str) -> bool:
    lowered = path.lower()
    vendor_markers = (
        ".venv",
        "venv/",
        "site-packages",
        "/_vendor/",
        "/vendor/",
        "\\vendor\\",
    )
    return any(marker in lowered for marker in vendor_markers)


def _prefer_candidates(candidates: list[str]) -> list[str]:
    non_vendor = [candidate for candidate in candidates if not _is_vendor_path(candidate)]
    if non_vendor:
        candidates = non_vendor

    src_candidates = [candidate for candidate in candidates if candidate.startswith("src/")]
    if src_candidates:
        return src_candidates

    return candidates


def _directory_name_matches(query: str, dirs: list[str]) -> list[str]:
    dir_query = query.lower().replace(" module", "").strip()
    return _dedupe([d for d in dirs if Path(d).name.lower() == dir_query])


def _stem_directory_matches(query: str, dirs: list[str]) -> list[str]:
    path = Path(query)
    if path.suffix.lower() != ".py":
        return []

    stem = path.stem.lower()
    return _dedupe([d for d in dirs if Path(d).name.lower() == stem])


def _unique_directory_result(
    raw_target: str,
    matches: list[str],
    reason: str,
    confidence: float,
    max_candidates: int,
) -> ResolvedTarget | None:
    matches = _prefer_candidates(matches)

    if len(matches) == 1:
        return ResolvedTarget(raw_target, matches[0], confidence, reason, matches)

    if len(matches) > 1:
        return ResolvedTarget(
            raw_target,
            None,
            min(confidence, 0.50),
            f"ambiguous_{reason}",
            matches[:max_candidates],
        )

    return None


def resolve_target(
    raw_target: str,
    scan: dict,
    *,
    max_candidates: int = 5,
) -> ResolvedTarget:
    """
    Resolve a vague LLM target into a concrete repo file/folder path.

    Resolution order:
    1. exact file path
    2. normalized module/dotted path
    3. filename match
    4. directory match
    5. fuzzy file/path match
    """

    if not raw_target or not isinstance(raw_target, str):
        return ResolvedTarget(
            raw_target=str(raw_target),
            resolved_target=None,
            confidence=0.0,
            reason="empty_or_invalid_target",
            candidates=[],
        )

    query = _normalize_query(raw_target)
    query_lower = query.lower()

    files, dirs = _build_repo_index(scan)
    files_lower_map = {f.lower(): f for f in files}
    dirs_lower_map = {d.lower(): d for d in dirs}

    if query_lower in files_lower_map:
        resolved = files_lower_map[query_lower]
        return ResolvedTarget(raw_target, resolved, 1.0, "exact_file_path", [resolved])

    if query_lower in dirs_lower_map:
        resolved = dirs_lower_map[query_lower]
        return ResolvedTarget(
            raw_target, resolved, 0.95, "exact_directory_path", [resolved]
        )

    stem_dir_result = _unique_directory_result(
        raw_target,
        _stem_directory_matches(query, dirs),
        "directory_from_file_stem_match",
        0.72,
        max_candidates,
    )
    if (
        Path(query).name.lower() in {"model.py", "models.py"}
        and stem_dir_result is not None
        and stem_dir_result.resolved_target is not None
    ):
        return stem_dir_result

    suffix_matches = [f for f in files if f.lower().endswith(query_lower)]
    suffix_matches = _dedupe(suffix_matches)

    if len(suffix_matches) == 1:
        return ResolvedTarget(
            raw_target,
            suffix_matches[0],
            0.90,
            "unique_suffix_file_match",
            suffix_matches,
        )

    if len(suffix_matches) > 1:
        if stem_dir_result is not None and stem_dir_result.resolved_target is not None:
            return stem_dir_result

        return ResolvedTarget(
            raw_target,
            None,
            0.60,
            "ambiguous_suffix_file_match",
            suffix_matches[:max_candidates],
        )

    filename_matches = [f for f in files if Path(f).name.lower() == query_lower]
    filename_matches = _dedupe(filename_matches)

    if len(filename_matches) == 1:
        return ResolvedTarget(
            raw_target,
            filename_matches[0],
            0.85,
            "unique_filename_match",
            filename_matches,
        )

    if len(filename_matches) > 1:
        if stem_dir_result is not None and stem_dir_result.resolved_target is not None:
            return stem_dir_result

        return ResolvedTarget(
            raw_target,
            None,
            0.55,
            "ambiguous_filename_match",
            filename_matches[:max_candidates],
        )

    dir_name_matches = _directory_name_matches(query, dirs)
    dir_name_result = _unique_directory_result(
        raw_target,
        dir_name_matches,
        "unique_directory_name_match",
        0.80,
        max_candidates,
    )
    if dir_name_result is not None:
        return dir_name_result

    possibilities = files + dirs
    close = get_close_matches(query, possibilities, n=max_candidates, cutoff=0.65)

    if len(close) == 1:
        return ResolvedTarget(raw_target, close[0], 0.70, "fuzzy_match", close)

    if len(close) > 1:
        return ResolvedTarget(raw_target, None, 0.45, "ambiguous_fuzzy_match", close)

    return ResolvedTarget(raw_target, None, 0.0, "no_match", [])


def resolve_action_target(action, scan: dict) -> ResolvedTarget:
    """Resolve best available target field from a RecommendedAction."""
    raw_candidates = [
        getattr(action, "target_area", ""),
        getattr(action, "action", ""),
    ]

    results = [resolve_target(raw, scan) for raw in raw_candidates]
    resolved_results = [result for result in results if result.resolved_target]

    if resolved_results:
        return max(resolved_results, key=lambda result: result.confidence)

    return results[0] if results else resolve_target("", scan)
