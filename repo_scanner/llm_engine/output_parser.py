# repo_scanner/llm_engine/output_parser.py
from __future__ import annotations

import re

from pydantic import ValidationError

from repo_scanner.llm_engine.output_schema import RepoDecision


class LLMOutputParseError(Exception):
    pass


def extract_json_object(text: str) -> str:
    """
    Extract the first JSON object from an LLM response.
    Handles pure JSON, JSON in markdown fences, and prose around JSON.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMOutputParseError("No JSON object found in LLM output.")

    return text[start : end + 1].strip()


def parse_repo_decision(text: str) -> RepoDecision:
    """Parse and validate an LLM response as a RepoDecision."""
    json_text = extract_json_object(text)

    try:
        return RepoDecision.model_validate_json(json_text)
    except ValidationError as exc:
        raise LLMOutputParseError(
            f"JSON did not match RepoDecision schema: {exc}"
        ) from exc
    except ValueError as exc:
        raise LLMOutputParseError(f"Invalid JSON: {exc}") from exc


def validate_repo_decision_grounding(
    decision: RepoDecision,
    known_files: list[dict],
) -> RepoDecision:
    known_paths = {
        file_info.get("path", "").replace("\\", "/")
        for file_info in known_files
        if isinstance(file_info, dict)
    }
    known_names = {
        path.rsplit("/", 1)[-1]
        for path in known_paths
        if path
    }
    name_counts: dict[str, int] = {}
    for path in known_paths:
        name = path.rsplit("/", 1)[-1]
        name_counts[name] = name_counts.get(name, 0) + 1

    def looks_file_like(value: str) -> bool:
        return any(
            value.endswith(extension)
            for extension in (
                ".py",
                ".js",
                ".ts",
                ".json",
                ".md",
                ".yaml",
                ".yml",
                ".toml",
                ".txt",
            )
        )

    def dotted_path_candidate(value: str) -> str | None:
        parts = value.strip().split(".")
        if len(parts) < 3:
            return None

        extension = parts[-1]
        filename = f"{parts[-2]}.{extension}"
        folders = "/".join(parts[:-2])
        return f"{folders}/{filename}" if folders else filename

    def normalize_grounded_file(value: str) -> str | None:
        normalized = value.replace("\\", "/").strip()
        if normalized in known_paths:
            return normalized
        if normalized in known_names and name_counts.get(normalized) == 1:
            return normalized

        dotted_candidate = dotted_path_candidate(normalized)
        if dotted_candidate and dotted_candidate in known_paths:
            return dotted_candidate

        return None

    grounded_inspect_next: list[str] = []
    for target in decision.inspect_next:
        if not looks_file_like(target):
            grounded_inspect_next.append(target)
            continue

        grounded = normalize_grounded_file(target)
        if grounded:
            grounded_inspect_next.append(grounded)
    decision.inspect_next = grounded_inspect_next

    grounded_actions = []
    for action in decision.recommended_actions:
        action_values = [action.target_area]
        if action.action_type in {"inspect_file", "inspect_module"}:
            action_values.append(action.action)

        normalized_values = [
            normalize_grounded_file(value)
            for value in action_values
            if looks_file_like(value)
        ]
        has_ungrounded_file_ref = any(
            looks_file_like(value) and normalize_grounded_file(value) is None
            for value in action_values
        )

        if has_ungrounded_file_ref:
            continue

        if looks_file_like(action.target_area) and normalized_values:
            action.target_area = normalized_values[0]
        if (
            action.action_type in {"inspect_file", "inspect_module"}
            and looks_file_like(action.action)
            and normalized_values
        ):
            action.action = normalized_values[-1]

        grounded_actions.append(action)

    decision.recommended_actions = grounded_actions
    absence_markers = (
        "no evidence",
        "no specific evidence",
        "no known",
        "not found",
        "absence of evidence",
    )
    decision.assumptions = [
        assumption
        for assumption in decision.assumptions
        if not (
            "security" in assumption.lower()
            and any(marker in assumption.lower() for marker in absence_markers)
        )
    ]

    return decision


def repo_decision_to_json(decision: RepoDecision) -> str:
    return decision.model_dump_json(indent=2)
