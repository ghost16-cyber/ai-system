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
    # Planner target resolution handles action and inspect_next grounding. This
    # validation layer only removes unsupported absence-of-evidence assumptions.
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
