from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVALUATION_REPORT_DIR = Path(".work")
EVALUATION_REPORT_FILENAME = "rag-evaluation.json"


class RuntimeEvaluationView:
    """Read-only surfacing of the existing retrieval evaluation harness's
    latest disposable report (`project_retrieval/evaluation/runner.py::
    write_reports`). Never calls `run_evaluation()` itself -- evaluation is
    only ever triggered explicitly (CLI/ops action), never automatically
    during a user request.
    """

    def __init__(self, report_dir: Path | None = None) -> None:
        self._report_path = (report_dir or DEFAULT_EVALUATION_REPORT_DIR) / EVALUATION_REPORT_FILENAME

    def latest(self) -> dict[str, Any] | None:
        if not self._report_path.exists():
            return None
        try:
            report = json.loads(self._report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        generated_at = datetime.fromtimestamp(
            self._report_path.stat().st_mtime, tz=timezone.utc
        )
        age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
        modes = report.get("modes", [])
        available_modes = tuple(
            item["mode"] for item in modes if item.get("available")
        )
        return {
            "generated_at": generated_at.isoformat(),
            "evaluation_age_seconds": age_seconds,
            "available_modes": available_modes,
            "guardrails_passed": report.get("guardrails_passed"),
            "modes": modes,
        }
