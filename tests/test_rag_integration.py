from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.project_analysis.model_synthesis.proposals import build_evidence_envelope
from backend.app.project_artifacts import ProjectArtifactStore, ProjectArtifactType
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane
from backend.app.project_retrieval import (
    CorpusIngestionRequest,
    ProjectRetrievalError,
    ProjectRetrievalService,
    RetrievalRequest,
    canonical_retrieval_authority_id,
)
from backend.app.project_retrieval.contracts import normalize_query
from backend.app.project_control.contracts import content_hash
from backend.app.project_retrieval.reranking import DeterministicLexicalReranker


def _fixture(tmp_path: Path):
    root = tmp_path / "repo"
    source = root / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def parse_config(value: str) -> dict:\n"
        "    # Parser preserves exact configuration keys.\n"
        "    return {'value': value}\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("SECRET=do-not-index\n", encoding="utf-8")
    database = tmp_path / "astra.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    canonical = CanonicalProjectService(control, artifacts)
    created = canonical.create_project(
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        repository_root=root,
        repository_root_fingerprint="root-fingerprint-1",
        actor_id="actor-1",
        idempotency_key="create-1",
        folder_authority={
            "status": "completed",
            "action_id": "workspace-1",
            "conversation_id": "conversation-1",
            "workspace_id": "workspace-1",
            "repository_root_fingerprint": "root-fingerprint-1",
        },
        specification={
            "specification_id": "spec-1",
            "specification_hash": "1" * 64,
            "revision": 1,
            "included_paths": ["src"],
            "excluded_paths": [],
            "allowed_operations": ["read"],
        },
        manifest={
            "manifest_hash": "2" * 64,
            "complete": True,
            "revision": 1,
            "entries": [{"path": "src/app.py", "sha256": "3" * 64}],
        },
        plan={
            "revision": 1,
            "acceptance_criteria": [],
            "work_units": [{"work_unit_id": "work-1", "expected_files": ["src/app.py"]}],
        },
    )
    retrieval = ProjectRetrievalService(database, control, artifacts)
    retrieval.initialize()
    run = control.get_project(created.project_run_id)
    scope = control.get_scope_revision(run.current_scope_revision_id)
    plan = control.get_plan_revision(run.current_plan_revision_id)
    state_hash = retrieval.compute_repository_state(
        root, scope.included_paths, scope.excluded_paths
    )
    binding = {
        "project_id": run.project_run_id,
        "conversation_id": run.conversation_id,
        "actor_id": run.actor_id,
        "workspace_id": run.workspace_id,
        "repository_root": run.repository_root,
        "scope_revision_id": scope.scope_revision_id,
        "scope_hash": scope.content_hash,
        "plan_revision_id": plan.plan_revision_id,
        "plan_hash": plan.content_hash,
        "repository_manifest_hash": run.current_manifest_hash,
        "repository_state_hash": state_hash,
        "expected_project_state_version": run.state_version,
        "authority_id": canonical_retrieval_authority_id(run),
    }
    return root, source, database, control, artifacts, retrieval, binding


def _ingest(retrieval: ProjectRetrievalService, binding: dict[str, object]):
    return retrieval.ingest_project_corpus(
        CorpusIngestionRequest(**binding, idempotency_key="ingest-1")
    )


def _request(binding: dict[str, object], **changes: object) -> RetrievalRequest:
    query = str(changes.pop("query", "configuration parser"))
    normalized = normalize_query(query)
    values = {
        **binding,
        "request_id": "retrieve-1",
        "query": query,
        "normalized_query": normalized,
        "query_hash": content_hash(normalized),
        "idempotency_key": "retrieve-idem-1",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(changes)
    return RetrievalRequest(**values)


def test_ingestion_retrieval_persistence_replay_and_phase5b_boundary(tmp_path: Path) -> None:
    _root, _source, database, control, artifacts, retrieval, binding = _fixture(tmp_path)
    generation = _ingest(retrieval, binding)
    result = retrieval.retrieve(_request(binding))
    replay = ProjectRetrievalService(database, control, artifacts).retrieve(_request(binding))

    assert generation.source_count == 1
    assert result.evidence_count >= 1
    assert replay.artifact_id == result.artifact_id
    assert replay.replayed is True
    assert artifacts.verify(result.artifact_id).artifact_type == ProjectArtifactType.RETRIEVAL_EVIDENCE

    attached = retrieval.phase5b_evidence(result.artifact_id, _request(binding))
    envelope = build_evidence_envelope(
        project_run_id=str(binding["project_id"]),
        workspace_id=str(binding["workspace_id"]),
        objective="Update the exact parser.",
        scope_revision_id=str(binding["scope_revision_id"]),
        plan_revision_id=str(binding["plan_revision_id"]),
        manifest_hash=str(binding["repository_manifest_hash"]),
        repository_state_identity=str(binding["repository_state_hash"]),
        evidence_identity="deterministic-evidence",
        evidence_source_identity="deterministic-source",
        evidence={"allowed_modify_paths": ["src/app.py"]},
        allowed_paths=("src/app.py",),
        retrieval_evidence=attached,
    )
    assert envelope.project_rag_enabled is True
    assert envelope.retrieval_evidence is not None
    assert envelope.retrieval_evidence.advisory_only is True
    assert envelope.retrieval_evidence.has_execution_authority is False


def test_stale_repository_and_replay_are_rejected(tmp_path: Path) -> None:
    root, source, _database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    _ingest(retrieval, binding)
    request = _request(binding)
    result = retrieval.retrieve(request)
    source.write_text("def changed():\n    return True\n", encoding="utf-8")

    with pytest.raises(ProjectRetrievalError, match="repository_state_mismatch"):
        retrieval.retrieve(request)
    with pytest.raises(ProjectRetrievalError):
        retrieval.phase5b_evidence(result.artifact_id, request)
    assert retrieval.compute_repository_state(root, ("src",), ()) != binding["repository_state_hash"]


def test_changed_source_creates_durable_invalidation_and_new_generation(
    tmp_path: Path,
) -> None:
    root, source, database, control, artifacts, retrieval, binding = _fixture(tmp_path)
    first_generation = _ingest(retrieval, binding)
    old_artifact = retrieval.retrieve(_request(binding))
    source.write_text("def parse_config(value):\n    return value.strip()\n", encoding="utf-8")
    new_state = retrieval.compute_repository_state(root, ("src",), ())
    updated_binding = {**binding, "repository_state_hash": new_state}

    second_generation = retrieval.ingest_project_corpus(
        CorpusIngestionRequest(**updated_binding, idempotency_key="ingest-2")
    )
    restarted = ProjectRetrievalService(database, control, artifacts)

    assert second_generation.generation_id != first_generation.generation_id
    assert restarted.get_retrieval_artifact(
        str(binding["project_id"]), old_artifact.artifact_id
    ).invalidated is True
    assert restarted.status(str(binding["project_id"])).invalidated is True


def test_exact_identity_isolation_and_replay_mismatch(tmp_path: Path) -> None:
    _root, _source, _database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    _ingest(retrieval, binding)
    retrieval.retrieve(_request(binding))
    with pytest.raises(ProjectRetrievalError, match="actor_mismatch"):
        retrieval.retrieve(_request({**binding, "actor_id": "attacker"}))
    with pytest.raises(ProjectRetrievalError, match="replay_mismatch"):
        retrieval.retrieve(_request(binding, query="different query"))


class _UnavailableReranker(DeterministicLexicalReranker):
    identity = "unavailable-reranker"

    def rerank(self, query, candidates):
        from backend.app.project_retrieval.reranking import RerankerUnavailable
        raise RerankerUnavailable("offline")


def test_unavailable_reranker_uses_bounded_deterministic_fallback(tmp_path: Path) -> None:
    _root, _source, database, control, artifacts, retrieval, binding = _fixture(tmp_path)
    fallback_service = ProjectRetrievalService(
        database, control, artifacts, reranker=_UnavailableReranker()
    )
    _ingest(fallback_service, binding)
    result = fallback_service.retrieve(_request(binding))
    assert result.reranker_identity.endswith(":fallback")
    assert result.evidence_count <= 8
