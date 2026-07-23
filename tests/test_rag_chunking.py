from __future__ import annotations

from datetime import datetime, timezone

from backend.app.project_retrieval.bm25 import rank_bm25
from backend.app.project_retrieval.chunking import MAX_CHARS, chunk_source
from backend.app.project_retrieval.hashing import (
    eligible_relative_path,
    normalize_relative_path,
)
from backend.app.project_retrieval.hybrid import hybrid_candidates


def test_chunking_identities_and_order_are_deterministic() -> None:
    text = "# Parser\n\n" + "\n".join(f"line {index}" for index in range(1_000))
    kwargs = {
        "source_id": "source-1",
        "project_id": "project-1",
        "relative_path": "docs/parser.md",
        "source_content_hash": "a" * 64,
        "text": text,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    first = chunk_source(**kwargs)
    second = chunk_source(**kwargs)
    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    assert [item.ordinal for item in first] == list(range(len(first)))
    assert all(len(item.text) <= MAX_CHARS for item in first)


def test_bm25_and_hybrid_tie_breaks_are_deterministic() -> None:
    documents = {"b": "parser parser", "a": "parser parser", "c": "unrelated"}
    first = rank_bm25("parser", documents, limit=3)
    second = rank_bm25("parser", documents, limit=3)
    assert first == second
    candidates = hybrid_candidates(
        chunks={
            "a": ("a.py", "source-a"),
            "b": ("b.py", "source-b"),
            "c": ("c.py", "source-c"),
        },
        bm25_scores=first,
        semantic_scores={"a": 0.5, "b": 0.5, "c": 0.0},
        limit=3,
    )
    assert [item.chunk_id for item in candidates[:2]] == ["a", "b"]


def test_path_policy_rejects_traversal_archives_and_secrets() -> None:
    assert eligible_relative_path("src/app.py")
    assert not eligible_relative_path("../other/app.py")
    assert not eligible_relative_path("/etc/passwd")
    assert not eligible_relative_path(".env")
    assert not eligible_relative_path("node_modules/a.js")
    assert not eligible_relative_path("corpus.zip")
    try:
        normalize_relative_path("../escape")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must fail closed")
