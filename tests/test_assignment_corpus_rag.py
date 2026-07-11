from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.assignments.copilot import run_assignment_copilot
from backend.app.main import create_app


BRIEF = """
Assignment 1: Kafka Reporting
Task: Build a Kafka pipeline and generate an assignment report with screenshots.
Analysis question: Explain the ingestion design and evidence.
"""


def _source() -> dict:
    return {
        "chunk_id": "assignment-guide-1",
        "source_path": "guides/reporting.md",
        "source_hash": "source",
        "chunk_index": 0,
        "chunk_hash": "chunk",
        "text": "Use verified screenshots and explain the pipeline design in the report.",
        "start_line": 3,
        "end_line": 5,
        "extension": ".md",
        "score": 0.9,
    }


def test_assignment_copilot_uses_corpus_retrieval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    monkeypatch.setattr(
        corpus_retrieval,
        "search_corpus_vectors",
        lambda *args, **kwargs: {"status": "ready", "results": [_source()]},
    )

    result = run_assignment_copilot(
        text=BRIEF,
        workspace_path=tmp_path,
        corpus_workspace_root=tmp_path,
    )

    assert result.corpus_retrieval_used is True
    assert result.corpus_context_count == 1
    assert result.corpus_sources[0].source_path == "guides/reporting.md"
    assert result.corpus_sources[0].text_preview.startswith("Use verified screenshots")
    assert result.tools_executed is False


def test_assignment_copilot_without_results_keeps_existing_behavior(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    monkeypatch.setattr(
        corpus_retrieval,
        "search_corpus_vectors",
        lambda *args, **kwargs: {"status": "ready", "results": []},
    )

    result = run_assignment_copilot(
        text=BRIEF,
        workspace_path=tmp_path,
        corpus_workspace_root=tmp_path,
    )

    assert result.corpus_retrieval_used is False
    assert result.corpus_retrieval_skip_reason == "no_relevant_results"
    assert result.corpus_sources == []
    assert result.action_plan.checklist
    assert result.report_draft.sections


def test_assignment_copilot_endpoint_supports_corpus_disable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    def unexpected_search(*args, **kwargs):
        raise AssertionError("disabled assignment retrieval must not search")

    monkeypatch.setattr(corpus_retrieval, "search_corpus_vectors", unexpected_search)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/copilot/run",
            json={"text": BRIEF, "workspace_path": ".", "use_corpus": False},
        )

    assert response.status_code == 200
    assert response.json()["corpus_retrieval_used"] is False
    assert response.json()["corpus_retrieval_skip_reason"] == "disabled"
