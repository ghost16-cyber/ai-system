from __future__ import annotations

from pathlib import Path

from backend.app.project_retrieval import CorpusIngestionRequest
from backend.app.runtime.background.handlers import DictHandlerRegistry
from backend.app.runtime.background.queue import RuntimeJobQueue
from backend.app.runtime.background.worker import RuntimeWorker
from backend.app.runtime.corpus import CorpusManager, make_corpus_reindex_handler
from tests.test_rag_integration import _fixture, _ingest, _request


def test_check_freshness_reflects_invalidation_state(tmp_path: Path) -> None:
    root, _source, database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    queue = RuntimeJobQueue(database)
    manager = CorpusManager(retrieval, queue)

    _ingest(retrieval, binding)
    # invalidate_for_binding_change targets existing retrieval *artifacts*,
    # so a retrieval must have happened before there is anything to invalidate.
    retrieval.retrieve(_request(binding))
    fresh = manager.check_freshness(binding["project_id"])
    assert fresh.fresh is True
    assert manager.is_valid(binding["project_id"]) is True

    manager.invalidate(binding["project_id"], reason="scope_changed", binding_hash="a" * 64)
    stale = manager.check_freshness(binding["project_id"])
    assert stale.fresh is False
    assert manager.is_valid(binding["project_id"]) is False


def test_check_freshness_detects_repository_state_drift(tmp_path: Path) -> None:
    """Category: automatic freshness -- content changes (and by extension
    added/deleted/renamed files, since compute_repository_state hashes every
    eligible file's content) must be detected without needing a fresh
    invalidation event."""
    root, source, database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    queue = RuntimeJobQueue(database)
    manager = CorpusManager(retrieval, queue)
    _ingest(retrieval, binding)

    stale_state_hash = binding["repository_state_hash"]
    source.write_text(
        "def parse_config(value: str) -> dict:\n"
        "    # Content changed after ingestion.\n"
        "    return {'value': value, 'changed': True}\n",
        encoding="utf-8",
    )
    freshness = manager.check_freshness(
        binding["project_id"],
        repository_root=root,
        included_paths=("src",),
        excluded_paths=(),
        expected_repository_state_hash=stale_state_hash,
    )
    assert freshness.fresh is False
    assert freshness.repository_changed is True
    assert freshness.reason == "repository_state_drift"


def test_schedule_reindex_enqueues_exactly_once_and_worker_ingests(tmp_path: Path) -> None:
    """Category: incremental indexing + exactly-once. Scheduling the same
    binding twice (same idempotency_key) must not enqueue a duplicate job,
    and the worker's handler must call the real ingest_project_corpus (chunk
    identity/lineage preserved by that existing method, not reimplemented
    here)."""
    root, source, database, control, artifacts, retrieval, binding = _fixture(tmp_path)
    queue = RuntimeJobQueue(database)
    manager = CorpusManager(retrieval, queue)

    request = CorpusIngestionRequest(**binding, idempotency_key="reindex-1")
    manager.schedule_reindex(request)
    manager.schedule_reindex(request)  # duplicate, same idempotency_key
    summary = queue.status_summary()
    assert summary["queued"] == 1

    handlers = DictHandlerRegistry()
    handlers.register("corpus_reindex", make_corpus_reindex_handler(retrieval))
    worker = RuntimeWorker(queue, handlers, worker_id="corpus-worker")
    processed = worker.run_once()

    assert processed is True
    status = retrieval.status(binding["project_id"])
    assert status.current_generation_id is not None
    assert status.active_source_count >= 1


def test_record_indexing_outcome_is_queryable_by_project(tmp_path: Path) -> None:
    from backend.app.runtime.persistence import RuntimePersistence

    root, _source, database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    queue = RuntimeJobQueue(database)
    persistence = RuntimePersistence(database)
    manager = CorpusManager(retrieval, queue, persistence=persistence)

    manager.record_indexing_outcome(
        project_run_id=binding["project_id"],
        generation_id="rag-generation-test",
        trigger="freshness_check",
        files_changed=2,
        duration_ms=120,
        outcome="completed",
    )
    history = persistence.indexing_history_for_project(binding["project_id"])
    assert len(history) == 1
    assert history[0]["files_changed"] == 2
    assert history[0]["outcome"] == "completed"


def test_reindex_scheduled_reflects_an_actual_queued_job_not_mere_staleness(
    tmp_path: Path,
) -> None:
    """A stale corpus with nothing queued must report reindex_scheduled as
    False -- staleness and "a reindex job actually exists" are independent
    facts, and reporting the former as the latter is dishonest telemetry."""

    root, _source, database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    queue = RuntimeJobQueue(database)
    manager = CorpusManager(retrieval, queue)
    _ingest(retrieval, binding)
    retrieval.retrieve(_request(binding))
    manager.invalidate(binding["project_id"], reason="scope_changed", binding_hash="a" * 64)

    assert manager.check_freshness(binding["project_id"]).fresh is False
    assert manager.reindex_scheduled(binding["project_id"]) is False

    request = CorpusIngestionRequest(**binding, idempotency_key="reindex-scheduled-check")
    manager.schedule_reindex(request)
    assert manager.reindex_scheduled(binding["project_id"]) is True

    handlers = DictHandlerRegistry()
    handlers.register("corpus_reindex", make_corpus_reindex_handler(retrieval))
    worker = RuntimeWorker(queue, handlers, worker_id="corpus-worker")
    worker.run_once()

    # Once the job completes, it is no longer active -- scheduled goes back
    # to False even though the corpus may still show as stale until the next
    # freshness check observes the new generation.
    assert manager.reindex_scheduled(binding["project_id"]) is False
