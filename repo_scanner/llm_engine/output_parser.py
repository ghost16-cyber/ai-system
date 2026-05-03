# repo_scanner/llm_engine/output_parser.py
from __future__ import annotations

import json
import re
from typing import Any

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
        data: Any = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise LLMOutputParseError(f"Invalid JSON: {exc}") from exc

    try:
        return RepoDecision.model_validate(data)
    except ValidationError as exc:
        raise LLMOutputParseError(
            f"JSON did not match RepoDecision schema: {exc}"
        ) from exc


def repo_decision_to_json(decision: RepoDecision) -> str:
    return decision.model_dump_json(indent=2)
