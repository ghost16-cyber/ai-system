from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.project_retrieval import ProjectRetrievalService
from backend.app.project_retrieval.contracts import CorpusIngestionRequest
from backend.app.runtime.background.queue import RuntimeJobQueue
from backend.app.runtime.contracts import CorpusFreshness
from backend.app.runtime.persistence import RuntimePersistence


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CorpusManager:
    """Owns corpus readiness/freshness/invalidation/reindex-scheduling.
    Never retrieves (`ProjectRetrievalService.retrieve`) or synthesizes.
    Every mutation delegates to ProjectRetrievalService's existing,
    already-authority-checked methods -- CorpusManager holds no chunking,
    hashing, or authority-binding logic of its own. Reindexing never
    fabricates a project's scope/authority binding: whoever schedules a
    reindex must supply the same exact `CorpusIngestionRequest` binding the
    project-bound caller already holds (mirroring how `ingest_project_corpus`
    itself requires it) -- CorpusManager only queues that binding for
    asynchronous, deterministic, exactly-once dispatch.
    """

    def __init__(
        self,
        project_retrieval_service: ProjectRetrievalService,
        job_queue: RuntimeJobQueue,
        *,
        persistence: RuntimePersistence | None = None,
    ) -> None:
        self._service = project_retrieval_service
        self._job_queue = job_queue
        self._persistence = persistence

    def is_valid(self, project_id: str) -> bool:
        status = self._service.status(project_id)
        return not status.invalidated

    def check_freshness(
        self,
        project_id: str,
        *,
        repository_root: Path | None = None,
        included_paths: tuple[str, ...] = (),
        excluded_paths: tuple[str, ...] = (),
        expected_repository_state_hash: str | None = None,
    ) -> CorpusFreshness:
        """Read-only freshness check. Without a repository root the check is
        scoped to what ProjectRetrievalService's own durable status already
        tracks (explicit invalidations); with a root+scope it additionally
        detects a live repository-state drift (covers added/deleted/renamed
        files and changed content, since `compute_repository_state` hashes
        every eligible file's content) by delegating to the existing
        `compute_repository_state` -- no new hashing logic here.
        """
        status = self._service.status(project_id)
        repository_changed = False
        if repository_root is not None and expected_repository_state_hash is not None:
            observed = self._service.compute_repository_state(
                repository_root, included_paths, excluded_paths
            )
            repository_changed = observed != expected_repository_state_hash

        fresh = (not status.invalidated) and (not repository_changed)
        reason = None
        if status.invalidated:
            reason = "; ".join(status.invalidation_reasons) or "invalidated"
        elif repository_changed:
            reason = "repository_state_drift"

        return CorpusFreshness(
            project_id=project_id,
            fresh=fresh,
            reason=reason,
            repository_changed=repository_changed,
            current_generation_id=status.current_generation_id,
            checked_at=_now(),
        )

    def reindex_scheduled(self, project_id: str) -> bool:
        """Whether a corpus_reindex job for this project is actually queued,
        claimed, or running -- distinct from staleness (check_freshness):
        a corpus can be stale with no reindex scheduled yet, and a reindex
        can be scheduled for a corpus that is not (or no longer) stale."""

        return self._job_queue.has_active_job(job_type="corpus_reindex", target_id=project_id)

    def schedule_reindex(
        self,
        binding: CorpusIngestionRequest,
        *,
        priority: int = 100,
    ) -> bool:
        """Enqueue a deterministic, exactly-once (idempotency_key-deduped)
        reindex job. The handler registered for job_type "corpus_reindex"
        (see background/handlers.py wiring in manager assembly) calls
        `ProjectRetrievalService.ingest_project_corpus(binding)` -- the exact
        existing entrypoint, unmodified -- which already preserves chunk
        ids/lineage and only re-chunks changed sources.
        """
        self._job_queue.enqueue(
            job_type="corpus_reindex",
            target_id=binding.project_id,
            idempotency_key=binding.idempotency_key,
            payload=binding.model_dump(mode="json"),
            priority=priority,
        )
        return True

    def invalidate(self, project_id: str, *, reason: str, binding_hash: str) -> int:
        return self._service.invalidate_for_binding_change(
            project_id, reason=reason, binding_hash=binding_hash
        )

    def record_indexing_outcome(
        self,
        *,
        project_run_id: str,
        generation_id: str | None,
        trigger: str,
        files_changed: int,
        duration_ms: int | None,
        outcome: str,
    ) -> None:
        if self._persistence is None:
            return
        self._persistence.record_indexing_history(
            history_id=str(uuid4()),
            project_run_id=project_run_id,
            generation_id=generation_id,
            trigger=trigger,
            files_changed=files_changed,
            duration_ms=duration_ms,
            outcome=outcome,
            detail={
                "generation_id": generation_id,
                "trigger": trigger,
                "files_changed": files_changed,
                "outcome": outcome,
            },
        )


def make_corpus_reindex_handler(project_retrieval_service: ProjectRetrievalService):
    """Builds the background-worker handler for job_type "corpus_reindex".
    A thin call into the existing `ingest_project_corpus` -- no chunking or
    hashing logic lives here."""

    def _handler(_target_id: str, payload: dict) -> bool:
        request = CorpusIngestionRequest.model_validate(payload)
        project_retrieval_service.ingest_project_corpus(request)
        return True

    return _handler
