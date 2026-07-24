from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.runtime.adapters import (
    LocalAIAdapter,
    ProjectControlAdapter,
    ProjectCoordinatorAdapter,
    ProviderAdapter,
    RetrievalAdapter,
    SimpleInitAdapter,
)
from backend.app.runtime.background.handlers import DictHandlerRegistry
from backend.app.runtime.background.queue import RuntimeJobQueue
from backend.app.runtime.background.worker import RuntimeWorker
from backend.app.runtime.caches import CacheRegistry
from backend.app.runtime.corpus import CorpusManager, make_corpus_reindex_handler
from backend.app.runtime.evaluation_view import RuntimeEvaluationView
from backend.app.runtime.manager import RuntimeManager
from backend.app.runtime.persistence import RuntimePersistence
from backend.app.runtime.protocol import SubsystemRegistration
from backend.app.runtime.recovery import RecoveryCoordinator
from backend.app.runtime.state_machine import RuntimeStateMachine
from backend.app.runtime.telemetry import TelemetryRegistry


def build_runtime_manager(
    *,
    database_path: str | Path,
    repository: Any,
    project_control: Any,
    project_artifact_store: Any,
    project_retrieval_service: Any,
    local_ai_service: Any,
    project_worker_queue: Any,
    project_mutation_engine: Any,
    project_coordinator: Any,
    synthesis_proposal_store: Any,
    job_queue: Any,
    rag_embedding: Any,
    rag_reranker: Any,
) -> RuntimeManager:
    """Assembles the one authoritative RuntimeManager from the already-
    constructed singletons `create_app()` builds. Construction only wraps
    existing objects in adapters (adapters.py) -- it never builds a second
    instance of any authority (ProjectControlPlane, ProjectRetrievalService,
    LocalAIService, etc).
    """
    persistence = RuntimePersistence(database_path)
    state_machine = RuntimeStateMachine(persistence)

    project_control_adapter = ProjectControlAdapter(project_control)
    retrieval_adapter = RetrievalAdapter(project_retrieval_service)
    local_ai_adapter = LocalAIAdapter(local_ai_service)
    provider_adapter = ProviderAdapter(rag_embedding, rag_reranker, local_ai_service)
    coordinator_adapter = ProjectCoordinatorAdapter(project_coordinator)

    # recover_method turns startup chat-runtime recovery (marking requests a
    # prior process left 'active' as 'interrupted') into an explicit
    # RecoveryCoordinator step instead of an implicit side effect of every
    # repository initialize() -- see AnalysisRepository.recover_interrupted_chat_requests.
    repository_adapter = SimpleInitAdapter(
        "repository", repository, recover_method=repository.recover_interrupted_chat_requests
    )
    artifact_adapter = SimpleInitAdapter("project_artifact_store", project_artifact_store)
    worker_queue_adapter = SimpleInitAdapter("project_worker_queue", project_worker_queue)
    mutation_engine_adapter = SimpleInitAdapter("project_mutation_engine", project_mutation_engine)
    synthesis_store_adapter = SimpleInitAdapter("synthesis_proposal_store", synthesis_proposal_store)
    job_queue_adapter = SimpleInitAdapter("job_queue", job_queue)

    # Reproduces the pre-Phase-8 lifespan()'s exact init order: repository,
    # project_control, project_artifact_store, project_retrieval,
    # project_worker_queue, project_mutation_engine, project_coordinator,
    # synthesis_proposal_store, local_ai, then job_queue last.
    registrations = (
        SubsystemRegistration(repository_adapter, init_order=0),
        SubsystemRegistration(project_control_adapter, init_order=1),
        SubsystemRegistration(artifact_adapter, init_order=2),
        SubsystemRegistration(retrieval_adapter, init_order=3),
        SubsystemRegistration(worker_queue_adapter, init_order=4),
        SubsystemRegistration(mutation_engine_adapter, init_order=5),
        SubsystemRegistration(coordinator_adapter, init_order=6),
        SubsystemRegistration(synthesis_store_adapter, init_order=7),
        SubsystemRegistration(local_ai_adapter, init_order=8),
        SubsystemRegistration(provider_adapter, init_order=9),
        SubsystemRegistration(job_queue_adapter, init_order=10),
    )

    runtime_job_queue = RuntimeJobQueue(database_path)
    handlers = DictHandlerRegistry()
    handlers.register("corpus_reindex", make_corpus_reindex_handler(project_retrieval_service))
    background_worker = RuntimeWorker(runtime_job_queue, handlers, worker_id="runtime-worker")

    corpus_manager = CorpusManager(
        project_retrieval_service, runtime_job_queue, persistence=persistence
    )
    cache_registry = CacheRegistry(persistence=persistence)
    telemetry_registry = TelemetryRegistry(persistence)
    recovery_coordinator = RecoveryCoordinator(persistence)
    evaluation_view = RuntimeEvaluationView()

    return RuntimeManager(
        registrations,
        state_machine=state_machine,
        persistence=persistence,
        project_control=project_control_adapter,
        retrieval=retrieval_adapter,
        local_ai=local_ai_adapter,
        providers=provider_adapter,
        corpus_manager=corpus_manager,
        background_worker=background_worker,
        job_queue=runtime_job_queue,
        cache_registry=cache_registry,
        telemetry_registry=telemetry_registry,
        recovery_coordinator=recovery_coordinator,
        evaluation_view=evaluation_view,
    )


__all__ = ["RuntimeManager", "build_runtime_manager"]
