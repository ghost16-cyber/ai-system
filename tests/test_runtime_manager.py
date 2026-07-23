from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.database.repository import AnalysisRepository
from backend.app.local_ai.contracts import CapabilityStatus
from backend.app.local_ai.service import LocalAIService
from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control.service import ProjectControlPlane
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_retrieval import (
    ProjectRetrievalService,
    build_retrieval_providers,
    retrieval_configuration_from_environment,
)
from backend.app.runtime.adapters import (
    LocalAIAdapter,
    ProjectControlAdapter,
    ProjectCoordinatorAdapter,
    ProviderAdapter,
    RetrievalAdapter,
    SimpleInitAdapter,
)
from backend.app.runtime.contracts import RuntimeState
from backend.app.runtime.manager import RuntimeManager
from backend.app.runtime.persistence import RuntimePersistence
from backend.app.runtime.protocol import SubsystemRegistration
from backend.app.runtime.state_machine import RuntimeStateError, RuntimeStateMachine


def _runtime(tmp_path: Path, *, name: str = "runtime.db"):
    """Shared builder for a RuntimeManager wired to real subsystem instances
    against a fresh database, reused by other test_runtime_*.py files."""
    database = tmp_path / name
    repository = AnalysisRepository(database)
    project_control = ProjectControlPlane(database)
    artifacts = ProjectArtifactStore(database)
    local_ai_service = LocalAIService(database)
    configuration = retrieval_configuration_from_environment()
    embedding, reranker = build_retrieval_providers(configuration, local_ai=local_ai_service)
    retrieval_service = ProjectRetrievalService(
        database, project_control, artifacts,
        embedding_provider=embedding, reranker=reranker,
    )
    coordinator = ProjectCoordinatorService(database, project_control)

    project_control_adapter = ProjectControlAdapter(project_control)
    local_ai_adapter = LocalAIAdapter(local_ai_service)
    retrieval_adapter = RetrievalAdapter(retrieval_service)
    coordinator_adapter = ProjectCoordinatorAdapter(coordinator)
    provider_adapter = ProviderAdapter(embedding, reranker, local_ai_service)
    repository_adapter = SimpleInitAdapter("repository", repository)
    artifact_adapter = SimpleInitAdapter("project_artifact_store", artifacts)

    registrations = (
        SubsystemRegistration(repository_adapter, init_order=0),
        SubsystemRegistration(project_control_adapter, init_order=1),
        SubsystemRegistration(artifact_adapter, init_order=2),
        SubsystemRegistration(retrieval_adapter, init_order=3),
        SubsystemRegistration(coordinator_adapter, init_order=4),
        SubsystemRegistration(local_ai_adapter, init_order=5),
        SubsystemRegistration(provider_adapter, init_order=6),
    )
    persistence = RuntimePersistence(database)
    state_machine = RuntimeStateMachine(persistence)
    manager = RuntimeManager(
        registrations,
        state_machine=state_machine,
        persistence=persistence,
        project_control=project_control_adapter,
        retrieval=retrieval_adapter,
        local_ai=local_ai_adapter,
        providers=provider_adapter,
    )
    return manager, persistence, database


def test_startup_reaches_ready_and_persists_the_transition(tmp_path: Path) -> None:
    manager, persistence, _database = _runtime(tmp_path)
    readiness = manager.initialize()

    assert readiness.ready is True
    assert readiness.state == RuntimeState.READY
    assert readiness.blocking_reasons == ()
    assert manager.state == RuntimeState.READY

    events = persistence.recent_state_events()
    assert len(events) == 1
    assert events[0]["from_state"] == "initializing"
    assert events[0]["to_state"] == "ready"


def test_shutdown_transitions_to_stopped_and_persists_every_step(tmp_path: Path) -> None:
    manager, persistence, _database = _runtime(tmp_path)
    manager.initialize()
    manager.shutdown()

    assert manager.state == RuntimeState.STOPPED
    events = persistence.recent_state_events()
    # ready (from init) + stopping + stopped
    assert len(events) == 3
    assert [row["to_state"] for row in reversed(events)] == ["ready", "stopping", "stopped"]


def test_never_fakes_ready_when_a_required_subsystem_did_not_initialize(
    tmp_path: Path,
) -> None:
    """readiness() is a strict AND of every check -- if project_control was
    never marked ready, the runtime must report DEGRADED, never READY."""
    manager, _persistence, _database = _runtime(tmp_path)
    # Simulate a subsystem that never completed initialize() by directly
    # asking readiness() before initialize() runs at all. Provider health is
    # independent of adapter init state (it reads the provider objects'
    # own readiness), so only the two init-gated checks are expected here.
    readiness = manager.readiness()
    assert readiness.ready is False
    assert readiness.state == RuntimeState.DEGRADED
    assert "project_control_not_ready" in readiness.blocking_reasons
    assert "project_retrieval_not_ready" in readiness.blocking_reasons


def test_state_machine_rejects_illegal_transitions(tmp_path: Path) -> None:
    manager, _persistence, _database = _runtime(tmp_path)
    manager.initialize()
    with pytest.raises(RuntimeStateError):
        manager._state_machine.transition(RuntimeState.STOPPED, trigger="illegal")
    # Illegal attempt must not have mutated state.
    assert manager.state == RuntimeState.READY


def test_state_machine_allowed_transition_table_is_closed() -> None:
    """Every transition not explicitly allowed must be rejected -- this
    directly proves "explicit transitions, no hidden state"."""
    state_machine = RuntimeStateMachine()
    all_states = tuple(RuntimeState)
    allowed_pairs = set()
    for from_state in all_states:
        for to_state in all_states:
            state_machine._state = from_state
            try:
                state_machine.transition(to_state, trigger="probe", persist=False)
                allowed_pairs.add((from_state, to_state))
            except RuntimeStateError:
                pass
    # Every allowed pair must be a genuine state change (no self-loops) and
    # STOPPED must never be reachable except via STOPPING.
    for from_state, to_state in allowed_pairs:
        assert from_state != to_state
    assert (RuntimeState.READY, RuntimeState.STOPPED) not in allowed_pairs
    assert (RuntimeState.STOPPING, RuntimeState.STOPPED) in allowed_pairs


def test_health_report_aggregates_every_subsystem_and_probes_hardware_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _persistence, _database = _runtime(tmp_path)
    manager.initialize()

    calls = {"count": 0}
    original = manager._local_ai.capability_report

    def _counting_report(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(manager._local_ai, "capability_report", _counting_report)
    report = manager.health()

    assert calls["count"] == 1
    subsystem_ids = {item.subsystem_id for item in report.subsystems}
    assert {
        "project_control", "project_retrieval", "local_ai",
        "rag_embedding_provider", "rag_reranker_provider",
        "repository", "project_artifact_store", "project_coordinator",
    } <= subsystem_ids
    assert report.overall == CapabilityStatus.READY
    assert report.runtime_state == RuntimeState.READY
