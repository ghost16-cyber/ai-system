from __future__ import annotations

import json
from pathlib import Path

from backend.app.runtime.evaluation_view import RuntimeEvaluationView


def test_latest_returns_none_when_no_report_exists(tmp_path: Path) -> None:
    view = RuntimeEvaluationView(report_dir=tmp_path)
    assert view.latest() is None


def test_latest_reads_the_existing_disposable_report_without_running_evaluation(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "rag-evaluation.json"
    report_path.write_text(
        json.dumps({
            "modes": [
                {"mode": "hybrid", "available": True},
                {"mode": "learned", "available": False},
            ],
            "guardrails_passed": True,
        }),
        encoding="utf-8",
    )
    view = RuntimeEvaluationView(report_dir=tmp_path)
    latest = view.latest()
    assert latest is not None
    assert latest["available_modes"] == ("hybrid",)
    assert latest["guardrails_passed"] is True
    assert latest["evaluation_age_seconds"] >= 0


def test_latest_returns_none_for_malformed_report(tmp_path: Path) -> None:
    (tmp_path / "rag-evaluation.json").write_text("{not-json", encoding="utf-8")
    view = RuntimeEvaluationView(report_dir=tmp_path)
    assert view.latest() is None
