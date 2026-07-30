from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_SPECIALIST_FEEDBACK_PATH = Path("data/specialists/specialist_feedback.jsonl")


def append_specialist_feedback(
    feedback: dict[str, Any],
    path: str | Path | None = None,
) -> dict[str, Any]:
    feedback_path = Path(path or DEFAULT_SPECIALIST_FEEDBACK_PATH)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "specialist": feedback.get("specialist"),
        "text": feedback.get("text"),
        "expected_label": feedback.get("expected_label"),
        "predicted_label": feedback.get("predicted_label"),
        "user_corrected_label": feedback.get("user_corrected_label"),
        "source": feedback.get("source"),
        "timestamp": feedback.get("timestamp")
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if "metadata" in feedback:
        row["metadata"] = feedback["metadata"]

    with feedback_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")

    return {
        "saved": True,
        "path": str(feedback_path),
        "feedback": row,
    }
