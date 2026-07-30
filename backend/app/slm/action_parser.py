from __future__ import annotations

import json
import re
from typing import Any


class ActionParseError(ValueError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        value = _extract_first_json_object(cleaned)

    if not isinstance(value, dict):
        raise ActionParseError("SLM output was not a JSON object.")

    return value


def _extract_first_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start == -1:
        raise ActionParseError("No JSON object found in SLM output.")

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ActionParseError(f"Invalid JSON object: {exc}") from exc

                if not isinstance(value, dict):
                    raise ActionParseError("Extracted JSON was not an object.")

                return value

    raise ActionParseError("Could not find a complete JSON object.")


def normalize_action_payload(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action") or payload.get("name")
    reason = payload.get("reason", "")
    args = payload.get("args", {})

    if not isinstance(action, str) or not action.strip():
        raise ActionParseError("Missing or invalid action.")

    if not isinstance(reason, str):
        reason = str(reason)

    if args is None:
        args = {}

    if not isinstance(args, dict):
        raise ActionParseError("args must be a JSON object.")

    return {
        "action": action.strip(),
        "reason": reason.strip(),
        "args": args,
    }
