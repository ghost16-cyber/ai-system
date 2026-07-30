from pathlib import Path

from backend.app.project_retrieval.evaluation.runner import (
    run_evaluation,
    write_reports,
)


def test_deterministic_evaluation_is_reproducible_and_reports_guardrails(
    tmp_path: Path,
) -> None:
    first = run_evaluation(deterministic_only=True)
    second = run_evaluation(deterministic_only=True)
    assert first.run_id == second.run_id
    assert [item.mode for item in first.modes] == [
        "bm25",
        "semantic",
        "hybrid",
        "hybrid_deterministic_rerank",
    ]
    assert first.guardrails_passed is True
    for item in first.modes:
        assert item.metrics is not None
        assert item.metrics.prompt_injection_authority_violation_count == 0
    json_path, markdown_path = write_reports(first, tmp_path)
    assert json_path.is_file()
    assert "Fixture-scale results" in markdown_path.read_text(encoding="utf-8")
