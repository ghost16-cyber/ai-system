from __future__ import annotations

import re
from pathlib import Path

from backend.app.folders.reader import ReadLimits, iter_project_files, line_excerpt, read_project_file
from backend.app.folders.safety import project_file_exclusion_reason, safe_relative_path
from backend.app.folders.scanner import validate_folder_root


def search_project(
    root: str | Path,
    query: str,
    *,
    exact_path: str | None = None,
    category: str | None = None,
    limits: ReadLimits | None = None,
) -> dict:
    limits = limits or ReadLimits()
    approved = validate_folder_root(str(root))
    normalized_query = " ".join((query or "").strip().lower().split())
    tokens = tuple(dict.fromkeys(re.findall(r"[a-zA-Z0-9_.-]{2,}", normalized_query)))
    requested = safe_relative_path(exact_path) if exact_path else None
    if requested:
        try:
            record = read_project_file(approved, requested, limits=limits)
        except FileNotFoundError:
            return {
                "query": query, "results": [], "inspected_files": 0,
                "skipped_files": 1, "total_bytes_read": 0,
                "read_budget_exhausted": False, "results_truncated": False,
            }
        if record["status"] != "readable":
            return {
                "query": query, "results": [], "inspected_files": 1,
                "skipped_files": 1, "total_bytes_read": 0,
                "read_budget_exhausted": False, "results_truncated": False,
            }
        end = min(int(record["line_count"]), limits.max_lines_per_excerpt)
        excerpt = line_excerpt(str(record["text"]), 1, end)[: limits.max_context_chars]
        return {
            "query": query,
            "results": [{
                "relative_path": requested, "score": 1000,
                "match_reason": "exact_relative_path", "start_line": 1,
                "end_line": end, "excerpt": excerpt,
                "truncated": bool(record["truncated"]) or len(excerpt) >= limits.max_context_chars,
            }],
            "inspected_files": 1, "skipped_files": 0,
            "total_bytes_read": int(record["bytes_read"]),
            "read_budget_exhausted": False, "results_truncated": False,
        }
    candidates: list[dict] = []
    inspected = skipped = total_bytes = 0
    budget_exhausted = False

    for path in iter_project_files(approved, max_files=limits.max_files + 1):
        relative = path.relative_to(approved).as_posix()
        if category and not _category_matches(relative, category):
            continue
        reason = project_file_exclusion_reason(path, approved)
        if reason:
            skipped += 1
            continue
        if inspected >= limits.max_files:
            budget_exhausted = True
            break
        inspected += 1
        path_lower = relative.lower()
        name_lower = path.name.lower()
        path_score, path_reason = _path_score(path_lower, name_lower, normalized_query, tokens, requested)
        record = read_project_file(approved, relative, limits=limits)
        if record["status"] != "readable":
            skipped += 1
            continue
        if total_bytes + int(record["bytes_read"]) > limits.max_total_bytes:
            budget_exhausted = True
            break
        total_bytes += int(record["bytes_read"])
        text = str(record["text"])
        content_score, line_number = _content_score(text, normalized_query, tokens)
        score = path_score + content_score
        if score <= 0:
            continue
        reason = path_reason or ("phrase_content_match" if content_score >= 30 else "keyword_content_match")
        start = max(1, line_number - 2)
        end = min(int(record["line_count"]), start + limits.max_lines_per_excerpt - 1)
        candidates.append({
            "relative_path": relative,
            "score": score,
            "match_reason": reason,
            "start_line": start,
            "end_line": end,
            "excerpt": line_excerpt(text, start, end),
            "truncated": bool(record["truncated"]),
        })

    candidates.sort(key=lambda item: (-int(item["score"]), str(item["relative_path"]).lower(), str(item["relative_path"])))
    results = []
    context_chars = 0
    for candidate in candidates[: limits.max_excerpts]:
        remaining = limits.max_context_chars - context_chars
        if remaining <= 0:
            budget_exhausted = True
            break
        item = dict(candidate)
        excerpt = str(item["excerpt"])
        if len(excerpt) > remaining:
            item["excerpt"] = excerpt[:remaining]
            item["truncated"] = True
            budget_exhausted = True
        context_chars += len(str(item["excerpt"]))
        results.append(item)
    return {
        "query": query,
        "results": results,
        "inspected_files": inspected,
        "skipped_files": skipped,
        "total_bytes_read": total_bytes,
        "read_budget_exhausted": budget_exhausted,
        "results_truncated": len(candidates) > limits.max_excerpts,
    }


def _path_score(path: str, name: str, query: str, tokens: tuple[str, ...], exact: str | None) -> tuple[int, str | None]:
    if exact and path == exact.lower():
        return 1000, "exact_relative_path"
    query_name = Path(query).name.lower() if query else ""
    if query_name and name == query_name:
        return 800, "exact_filename"
    if query_name and (name.startswith(query_name) or query_name.startswith(name)):
        return 600, "strong_filename_match"
    component_matches = sum(1 for token in tokens if token in path.split("/"))
    filename_matches = sum(1 for token in tokens if token in name)
    score = component_matches * 120 + filename_matches * 180
    return score, "path_component_match" if score else None


def _content_score(text: str, phrase: str, tokens: tuple[str, ...]) -> tuple[int, int]:
    lowered = text.lower()
    lines = lowered.splitlines()
    if phrase and phrase in lowered:
        line = next((i for i, value in enumerate(lines, 1) if phrase in value), 1)
        return 30 + min(20, len(tokens) * 3), line
    matched = [token for token in tokens if token in lowered]
    if not matched:
        return 0, 1
    line = next((i for i, value in enumerate(lines, 1) if any(token in value for token in matched)), 1)
    return (20 if len(matched) > 1 else 8) + len(matched), line


def _category_matches(relative: str, category: str) -> bool:
    lower = relative.lower()
    suffix = Path(lower).suffix
    if category == "tests":
        return "/test" in f"/{lower}" or Path(lower).name.startswith("test_") or ".test." in lower
    if category == "source":
        return suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".go", ".rs"}
    if category == "configuration":
        return suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
    if category == "documentation":
        return suffix in {".md", ".txt"}
    if category == "manifests":
        return Path(lower).name in {"package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod"}
    return True
