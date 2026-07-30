from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.rag.corpus_retrieval import retrieve_corpus_context


def _search_response(*results: dict) -> dict:
    return {"status": "ready", "results": list(results)}


def _source(
    *,
    chunk_id: str = "chunk-1",
    source_path: str = "docs/assignments.md",
    score: float = 0.8,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_path": source_path,
        "source_hash": "source-hash",
        "chunk_index": 2,
        "chunk_hash": "chunk-hash",
        "text": "The assignment report is generated from the extracted brief and evidence checklist.",
        "start_line": 10,
        "end_line": 14,
        "extension": ".md",
        "score": score,
    }


def _training_examples(root: Path) -> list[dict]:
    path = root / "data" / "training" / "intent_examples.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_relevant_chat_uses_corpus_metadata_prompt_and_logs_training_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    monkeypatch.setattr(
        corpus_retrieval,
        "search_corpus_vectors",
        lambda *args, **kwargs: _search_response(_source()),
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={
                "message": "How is the assignment report generated?",
                "use_rag": False,
                "use_corpus": True,
            },
        )
        runs = client.get("/chat/runs")

    assert response.status_code == 200
    body = response.json()
    assert body["corpus_retrieval_used"] is True
    assert body["corpus_context_count"] == 1
    assert body["corpus_sources"] == [
        {
            "source_path": "docs/assignments.md",
            "chunk_id": "chunk-1",
            "chunk_index": 2,
            "start_line": 10,
            "end_line": 14,
            "score": 0.8,
            "text_preview": "The assignment report is generated from the extracted brief and evidence checklist.",
        }
    ]
    assert runs.json()["items"][0]["corpus_sources"] == body["corpus_sources"]
    assert len(_training_examples(tmp_path)) == 1


def test_chat_greeting_and_explicit_disable_skip_corpus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    def unexpected_search(*args, **kwargs):
        raise AssertionError("corpus search should have been gated")

    monkeypatch.setattr(corpus_retrieval, "search_corpus_vectors", unexpected_search)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        greeting = client.post(
            "/chat/run",
            json={"message": "Hello", "use_rag": False, "use_corpus": True},
        )
        disabled = client.post(
            "/chat/run",
            json={
                "message": "Explain assignment report generation",
                "use_rag": True,
                "use_corpus": False,
            },
        )

    assert greeting.json()["corpus_retrieval_skip_reason"] == "greeting"
    assert disabled.json()["corpus_retrieval_skip_reason"] == "disabled"
    assert greeting.json()["corpus_retrieval_used"] is False
    assert disabled.json()["corpus_retrieval_used"] is False


def test_chat_missing_store_and_empty_results_fall_back_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    with TestClient(create_app(tmp_path / "missing.db", workspace_root=tmp_path)) as client:
        missing = client.post(
            "/chat/run",
            json={
                "message": "Explain assignment report generation",
                "use_rag": False,
                "use_corpus": True,
            },
        )

    monkeypatch.setattr(
        corpus_retrieval,
        "search_corpus_vectors",
        lambda *args, **kwargs: _search_response(),
    )
    with TestClient(create_app(tmp_path / "empty.db", workspace_root=tmp_path)) as client:
        empty = client.post(
            "/chat/run",
            json={
                "message": "Explain assignment report generation",
                "use_rag": False,
                "use_corpus": True,
            },
        )

    assert missing.status_code == 200
    assert missing.json()["corpus_retrieval_skip_reason"] == "vector_store_unavailable"
    assert empty.status_code == 200
    assert empty.json()["corpus_retrieval_skip_reason"] == "no_relevant_results"
    assert empty.json()["assistant_response"]


def test_corpus_retrieval_excludes_low_scores_and_removes_duplicates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.rag import corpus_retrieval

    duplicate = _source()
    monkeypatch.setattr(
        corpus_retrieval,
        "search_corpus_vectors",
        lambda *args, **kwargs: _search_response(
            _source(score=0.1),
            duplicate,
            dict(duplicate),
            _source(chunk_id="chunk-2", source_path="docs/other.md", score=0.7),
        ),
    )

    result = retrieve_corpus_context(
        "Explain assignment report generation",
        workspace_root=tmp_path,
        minimum_score=0.2,
    )

    assert result.used is True
    assert [source.chunk_id for source in result.sources] == ["chunk-1", "chunk-2"]
    assert all(source.score >= 0.2 for source in result.sources)
