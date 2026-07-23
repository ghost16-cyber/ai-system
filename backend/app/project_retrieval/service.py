from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import ProjectControlPlane
from backend.app.project_control.contracts import canonical_json, content_hash
from backend.app.project_retrieval.bindings import validate_project_binding
from backend.app.project_retrieval.bm25 import rank_bm25
from backend.app.project_retrieval.chunking import MAX_SOURCE_BYTES, chunk_source
from backend.app.project_retrieval.contracts import (
    CHUNKING_POLICY_VERSION,
    EMBEDDING_POLICY_VERSION,
    CorpusGeneration,
    CorpusIngestionRequest,
    CorpusSource,
    DocumentChunk,
    FreshnessStatus,
    BindingStatus,
    RetrievalArtifactCollection,
    RetrievalCandidate,
    RetrievalEvidenceArtifact,
    RetrievalEvidenceItem,
    RetrievalPhase5BEvidence,
    RetrievalRequest,
    RetrievalStatus,
    SourceKind,
)
from backend.app.project_retrieval.hashing import (
    eligible_relative_path,
    exact_bytes_hash,
    path_in_scope,
    repository_state_hash,
    resolve_safe_path,
)
from backend.app.project_retrieval.hybrid import hybrid_candidates
from backend.app.project_retrieval.persistence import RetrievalStore
from backend.app.project_retrieval.reranking import (
    DeterministicLexicalReranker,
    Reranker,
    RerankerUnavailable,
    validate_rerank,
)
from backend.app.project_retrieval.semantic import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingUnavailable,
    cosine,
)


MAX_FILES = 1_000
MAX_TOTAL_BYTES = 20_000_000
MAX_PROJECT_CHUNKS = 10_000
MAX_EMBEDDING_BATCH = 128


class ProjectRetrievalError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class ProjectRetrievalService:
    """Project-bound advisory retrieval; never a project lifecycle writer."""

    def __init__(
        self,
        database_path: str | Path,
        control: ProjectControlPlane,
        artifacts: ProjectArtifactStore,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.control = control
        self.artifacts = artifacts
        self.store = RetrievalStore(database_path)
        self.embedding = embedding_provider or DeterministicEmbeddingProvider()
        self.reranker = reranker or DeterministicLexicalReranker()

    def initialize(self) -> None:
        self.store.initialize()

    def ingest_project_corpus(self, request: CorpusIngestionRequest) -> CorpusGeneration:
        try:
            run, scope, _ = validate_project_binding(self.control, request)
        except ValueError as exc:
            raise ProjectRetrievalError(str(exc)) from exc
        files = self._eligible_files(
            Path(run.repository_root),
            scope.included_paths,
            scope.excluded_paths,
        )
        observed_state = repository_state_hash(
            [(relative, exact_bytes_hash(data)) for relative, data in files]
        )
        if observed_state != request.repository_state_hash:
            raise ProjectRetrievalError("repository_state_mismatch")
        material = {
            "project_id": request.project_id,
            "repository_root": request.repository_root,
            "repository_state_hash": request.repository_state_hash,
            "repository_manifest_hash": request.repository_manifest_hash,
            "scope_revision_id": request.scope_revision_id,
            "scope_hash": request.scope_hash,
            "chunking_policy_version": request.chunking_policy_version,
            "sources": [(path, exact_bytes_hash(data)) for path, data in files],
        }
        generation_id = f"rag-generation-{content_hash(material)[:24]}"
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT generation_json FROM rag_corpus_generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        if existing is not None:
            return CorpusGeneration.model_validate_json(existing["generation_json"]).model_copy(
                update={"replayed": True}
            )

        now = _now()
        source_chunks: list[tuple[CorpusSource, tuple[DocumentChunk, ...], bytes]] = []
        for relative, data in files:
            digest = exact_bytes_hash(data)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            source_material = {
                "project_id": run.project_run_id,
                "relative_path": relative,
                "content_hash": digest,
                "manifest_hash": request.repository_manifest_hash,
                "scope_hash": request.scope_hash,
                "chunking_policy": CHUNKING_POLICY_VERSION,
            }
            source_id = f"rag-source-{content_hash(source_material)[:32]}"
            source = CorpusSource(
                source_id=source_id,
                project_id=run.project_run_id,
                repository_root=run.repository_root,
                relative_path=relative,
                source_kind=SourceKind.REPOSITORY_TEXT_FILE,
                content_hash=digest,
                byte_size=len(data),
                media_type=_media_type(relative),
                repository_manifest_hash=request.repository_manifest_hash,
                repository_state_hash=request.repository_state_hash,
                scope_revision_id=request.scope_revision_id,
                scope_hash=request.scope_hash,
                ingested_at=now,
                freshness_observed_at=now,
                metadata={"truncated": False, "generation_id": generation_id},
            )
            chunks = chunk_source(
                source_id=source_id,
                project_id=run.project_run_id,
                relative_path=relative,
                source_content_hash=digest,
                text=text,
                created_at=now,
            )
            source_chunks.append((source, chunks, data))
        total_chunks = sum(len(chunks) for _, chunks, _ in source_chunks)
        if total_chunks > MAX_PROJECT_CHUNKS:
            raise ProjectRetrievalError("project_chunk_limit_exceeded")

        generation = CorpusGeneration(
            generation_id=generation_id,
            project_id=run.project_run_id,
            repository_state_hash=request.repository_state_hash,
            repository_manifest_hash=request.repository_manifest_hash,
            scope_revision_id=request.scope_revision_id,
            scope_hash=request.scope_hash,
            chunking_policy_version=CHUNKING_POLICY_VERSION,
            source_count=len(source_chunks),
            chunk_count=total_chunks,
            total_bytes=sum(len(data) for _, _, data in source_chunks),
            created_at=now,
        )
        self._persist_ingestion(generation, source_chunks)
        self._audit("rag_ingestion_completed", run.project_run_id, {
            "generation_id": generation_id,
            "repository_state_hash": request.repository_state_hash,
            "source_count": generation.source_count,
            "chunk_count": generation.chunk_count,
        })
        return generation

    def retrieve(self, request: RetrievalRequest) -> RetrievalEvidenceArtifact:
        fingerprint = content_hash(request.model_dump(mode="json"))
        try:
            run, scope, _ = validate_project_binding(self.control, request)
        except ValueError as exc:
            raise ProjectRetrievalError(str(exc)) from exc
        observed = self.compute_repository_state(
            Path(run.repository_root), scope.included_paths, scope.excluded_paths
        )
        if observed != request.repository_state_hash:
            self.invalidate_for_binding_change(
                request.project_id, reason="repository_state_changed",
                binding_hash=content_hash(request.model_dump(mode="json")),
            )
            raise ProjectRetrievalError("repository_state_mismatch")
        replay = self._replay(request, fingerprint)
        if replay is not None:
            return replay
        self._claim_request(request, fingerprint)
        chunks = self._active_chunks(request)
        if not chunks:
            self._fail_request(request.request_id)
            raise ProjectRetrievalError("retrieval_unavailable")
        documents = {item.chunk_id: item.text for item in chunks}
        bm25 = rank_bm25(request.normalized_query, documents, limit=request.max_candidates)
        try:
            query_vector = self.embedding.embed((request.normalized_query,))[0]
            vectors = self._embedding_vectors(chunks)
        except (EmbeddingUnavailable, ValueError) as exc:
            self._fail_request(request.request_id)
            raise ProjectRetrievalError("embedding_unavailable") from exc
        semantic = {
            chunk.chunk_id: cosine(query_vector, vectors[chunk.chunk_id])
            for chunk in chunks
        }
        chunk_bindings = {
            item.chunk_id: (item.relative_path, item.source_id) for item in chunks
        }
        candidates = hybrid_candidates(
            chunks=chunk_bindings,
            bm25_scores=bm25,
            semantic_scores=semantic,
            limit=request.max_candidates,
        )
        rerank_input = tuple(
            (candidate, documents[candidate.chunk_id])
            for candidate in candidates[: request.max_rerank]
        )
        reranker_identity = self.reranker.identity
        reranker_version = self.reranker.version
        fallback = False
        try:
            rerank_scores = validate_rerank(
                tuple(item[0] for item in rerank_input),
                self.reranker.rerank(request.normalized_query, rerank_input),
            )
        except (RerankerUnavailable, ValueError):
            fallback = True
            deterministic = DeterministicLexicalReranker()
            reranker_identity = f"{deterministic.identity}:fallback"
            reranker_version = deterministic.version
            rerank_scores = validate_rerank(
                tuple(item[0] for item in rerank_input),
                deterministic.rerank(request.normalized_query, rerank_input),
            )
            self._audit("rag_reranker_fallback", request.project_id, {
                "request_id": request.request_id,
                "configured_reranker": self.reranker.identity,
            })
        candidate_by_id = {item.chunk_id: item for item in candidates}
        chunk_by_id = {item.chunk_id: item for item in chunks}
        ranked_ids = sorted(
            rerank_scores,
            key=lambda chunk_id: (
                -rerank_scores[chunk_id],
                -candidate_by_id[chunk_id].hybrid_score,
                chunk_by_id[chunk_id].relative_path,
                chunk_by_id[chunk_id].ordinal,
                chunk_id,
            ),
        )
        evidence = tuple(
            self._evidence_item(
                chunk_by_id[chunk_id],
                candidate_by_id[chunk_id],
                rerank_scores[chunk_id],
                rank,
            )
            for rank, chunk_id in enumerate(ranked_ids[: request.max_evidence], start=1)
        )
        base_payload = {
            "schema_version": "astra.rag.canonical-artifact-payload.v1",
            "request_binding": request.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "candidate_count": len(candidates),
            "reranked_count": len(rerank_input),
            "embedding": {
                "identity": self.embedding.identity,
                "version": self.embedding.version,
            },
            "reranker": {
                "identity": reranker_identity,
                "version": reranker_version,
                "fallback": fallback,
            },
            "trust": "untrusted_retrieved_content",
            "advisory_only": True,
            "has_execution_authority": False,
            "has_approval_authority": False,
            "has_mutation_authority": False,
        }
        canonical_artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.RETRIEVAL_EVIDENCE,
            binding=ProjectArtifactBinding(
                project_run_id=run.project_run_id,
                plan_revision_id=request.plan_revision_id,
                scope_revision_id=request.scope_revision_id,
                manifest_hash=request.repository_manifest_hash,
                authority_hash=request.authority_id,
            ),
            payload=base_payload,
            evidence_references=tuple({
                "evidence_id": item.evidence_id,
                "chunk_id": item.chunk_id,
                "source_id": item.source_id,
                "text_hash": item.text_hash,
            } for item in evidence),
        ))
        now = _now()
        artifact = RetrievalEvidenceArtifact(
            artifact_id=canonical_artifact.artifact_id,
            artifact_hash=canonical_artifact.content_hash,
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            actor_id=request.actor_id,
            workspace_id=request.workspace_id,
            repository_root=request.repository_root,
            request_id=request.request_id,
            query_hash=request.query_hash,
            scope_revision_id=request.scope_revision_id,
            scope_hash=request.scope_hash,
            plan_revision_id=request.plan_revision_id,
            plan_hash=request.plan_hash,
            repository_manifest_hash=request.repository_manifest_hash,
            repository_state_hash=request.repository_state_hash,
            project_state_version=request.expected_project_state_version,
            authority_id=request.authority_id,
            retrieval_policy_version=request.retrieval_policy_version,
            chunking_policy_version=CHUNKING_POLICY_VERSION,
            embedding_model_identity=self.embedding.identity,
            embedding_model_version=self.embedding.version,
            reranker_identity=reranker_identity,
            reranker_version=reranker_version,
            candidate_count=len(candidates),
            reranked_count=len(rerank_input),
            evidence_count=len(evidence),
            evidence=evidence,
            created_at=now,
            freshness_checked_at=now,
        )
        self._complete_request(request, fingerprint, candidates, artifact)
        self._audit("rag_retrieval_completed", request.project_id, {
            "request_id": request.request_id,
            "artifact_id": artifact.artifact_id,
            "artifact_hash": artifact.artifact_hash,
            "evidence_count": artifact.evidence_count,
        })
        return artifact

    def get_retrieval_artifact(
        self, project_id: str, artifact_id: str
    ) -> RetrievalEvidenceArtifact:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT artifact_json FROM rag_retrieval_artifacts "
                "WHERE artifact_id = ? AND project_run_id = ?",
                (artifact_id, project_id),
            ).fetchone()
        if row is None:
            raise ProjectRetrievalError("retrieval_artifact_not_found")
        artifact = RetrievalEvidenceArtifact.model_validate_json(row["artifact_json"])
        invalidated = self._artifact_invalidated(artifact_id)
        return artifact.model_copy(update={"invalidated": invalidated})

    def list_retrieval_artifacts(
        self, project_id: str, *, limit: int = 100
    ) -> RetrievalArtifactCollection:
        self.control.get_project(project_id)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT artifact_id FROM rag_retrieval_artifacts "
                "WHERE project_run_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, max(1, min(limit, 500))),
            ).fetchall()
        items = tuple(self.get_retrieval_artifact(project_id, row["artifact_id"]) for row in rows)
        return RetrievalArtifactCollection(project_id=project_id, items=items, count=len(items))

    def status(self, project_id: str) -> RetrievalStatus:
        self.control.get_project(project_id)
        with self.store.connect() as connection:
            generation = connection.execute(
                "SELECT generation_id, created_at FROM rag_corpus_generations "
                "WHERE project_run_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            source_count = connection.execute(
                "SELECT COUNT(*) FROM rag_sources WHERE project_run_id = ? AND active = 1",
                (project_id,),
            ).fetchone()[0]
            chunk_count = connection.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE project_run_id = ? AND active = 1",
                (project_id,),
            ).fetchone()[0]
            embedding_count = connection.execute(
                "SELECT COUNT(*) FROM rag_embeddings e "
                "JOIN rag_chunks c ON c.chunk_id = e.chunk_id "
                "JOIN rag_sources s ON s.source_id = c.source_id "
                "WHERE c.project_run_id = ? AND c.active = 1 AND s.active = 1 "
                "AND e.model_identity = ? AND e.model_version = ? "
                "AND e.embedding_policy_version = ?",
                (
                    project_id,
                    self.embedding.identity,
                    self.embedding.version,
                    EMBEDDING_POLICY_VERSION,
                ),
            ).fetchone()[0]
            reasons = tuple(row[0] for row in connection.execute(
                "SELECT DISTINCT reason FROM rag_invalidations WHERE project_run_id = ? "
                "ORDER BY reason", (project_id,)
            ).fetchall())
        return RetrievalStatus(
            project_id=project_id,
            current_generation_id=generation["generation_id"] if generation else None,
            active_source_count=int(source_count),
            active_chunk_count=int(chunk_count),
            embedding_ready=bool(chunk_count) and int(embedding_count) == int(chunk_count),
            embedding_identity=f"{self.embedding.identity}:{self.embedding.version}",
            reranker_identity=f"{self.reranker.identity}:{self.reranker.version}",
            last_ingestion_at=(
                datetime.fromisoformat(generation["created_at"]) if generation else None
            ),
            invalidated=bool(reasons),
            invalidation_reasons=reasons,
        )

    def verify_artifact_freshness(
        self, artifact_id: str, binding: RetrievalRequest
    ) -> RetrievalEvidenceArtifact:
        artifact = self.get_retrieval_artifact(binding.project_id, artifact_id)
        if artifact.invalidated:
            raise ProjectRetrievalError("artifact_invalidated")
        try:
            validate_project_binding(self.control, binding)
        except ValueError as exc:
            raise ProjectRetrievalError(str(exc)) from exc
        exact = (
            artifact.project_id == binding.project_id
            and artifact.actor_id == binding.actor_id
            and artifact.conversation_id == binding.conversation_id
            and artifact.workspace_id == binding.workspace_id
            and artifact.repository_root == binding.repository_root
            and artifact.scope_revision_id == binding.scope_revision_id
            and artifact.scope_hash == binding.scope_hash
            and artifact.plan_revision_id == binding.plan_revision_id
            and artifact.plan_hash == binding.plan_hash
            and artifact.repository_manifest_hash == binding.repository_manifest_hash
            and artifact.repository_state_hash == binding.repository_state_hash
            and artifact.project_state_version == binding.expected_project_state_version
            and artifact.authority_id == binding.authority_id
        )
        if not exact:
            raise ProjectRetrievalError("retrieval_artifact_binding_mismatch")
        run = self.control.get_project(binding.project_id)
        scope = self.control.get_scope_revision(binding.scope_revision_id)
        observed_state = self.compute_repository_state(
            Path(run.repository_root), scope.included_paths, scope.excluded_paths
        )
        if observed_state != binding.repository_state_hash:
            self.invalidate_for_binding_change(
                binding.project_id,
                reason="repository_state_changed",
                binding_hash=content_hash(binding.model_dump(mode="json")),
            )
            raise ProjectRetrievalError("repository_state_mismatch")
        active_chunks = {chunk.chunk_id: chunk for chunk in self._active_chunks(binding)}
        for item in artifact.evidence:
            chunk = active_chunks.get(item.chunk_id)
            if (
                chunk is None
                or chunk.normalized_text_hash != item.text_hash
                or chunk.source_content_hash != item.source_content_hash
            ):
                raise ProjectRetrievalError("retrieval_source_stale")
        canonical = self.artifacts.verify(
            artifact.artifact_id,
            expected_content_hash=artifact.artifact_hash,
        )
        if canonical.artifact_type != ProjectArtifactType.RETRIEVAL_EVIDENCE:
            raise ProjectRetrievalError("retrieval_artifact_type_mismatch")
        return artifact.model_copy(update={"freshness_checked_at": _now()})

    def phase5b_evidence(
        self, artifact_id: str, binding: RetrievalRequest
    ) -> RetrievalPhase5BEvidence:
        """Load and revalidate evidence before it crosses the Phase 5B boundary."""
        artifact = self.verify_artifact_freshness(artifact_id, binding)
        return RetrievalPhase5BEvidence(
            retrieval_artifact_id=artifact.artifact_id,
            retrieval_artifact_hash=artifact.artifact_hash,
            project_id=artifact.project_id,
            scope_revision_id=artifact.scope_revision_id,
            scope_hash=artifact.scope_hash,
            plan_revision_id=artifact.plan_revision_id,
            plan_hash=artifact.plan_hash,
            repository_manifest_hash=artifact.repository_manifest_hash,
            repository_state_hash=artifact.repository_state_hash,
            project_state_version=artifact.project_state_version,
            authority_id=artifact.authority_id,
            evidence=artifact.evidence,
        )

    def invalidate_for_binding_change(
        self, project_id: str, *, reason: str, binding_hash: str
    ) -> int:
        now = _now()
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            artifacts = connection.execute(
                "SELECT artifact_id FROM rag_retrieval_artifacts WHERE project_run_id = ?",
                (project_id,),
            ).fetchall()
            count = 0
            for row in artifacts:
                invalidation_id = f"rag-invalidation-{content_hash([row['artifact_id'], reason, binding_hash])[:24]}"
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO rag_invalidations "
                    "(invalidation_id, project_run_id, target_type, target_id, reason, "
                    "binding_hash, invalidation_json, created_at) VALUES (?, ?, 'artifact', ?, ?, ?, ?, ?)",
                    (
                        invalidation_id, project_id, row["artifact_id"], reason,
                        binding_hash, canonical_json({
                            "invalidation_id": invalidation_id,
                            "target_id": row["artifact_id"],
                            "reason": reason,
                            "binding_hash": binding_hash,
                            "created_at": now.isoformat(),
                        }), now.isoformat(),
                    ),
                )
                count += cursor.rowcount
            connection.execute("COMMIT")
        if count:
            self._audit("rag_retrieval_invalidated", project_id, {
                "reason": reason, "artifact_count": count, "binding_hash": binding_hash,
            })
        return count

    def compute_repository_state(
        self,
        root: Path,
        included_paths: tuple[str, ...],
        excluded_paths: tuple[str, ...],
    ) -> str:
        files = self._eligible_files(root, included_paths, excluded_paths)
        return repository_state_hash(
            [(relative, exact_bytes_hash(data)) for relative, data in files]
        )

    def _eligible_files(
        self,
        root: Path,
        included_paths: tuple[str, ...],
        excluded_paths: tuple[str, ...],
    ) -> list[tuple[str, bytes]]:
        canonical_root = root.resolve(strict=True)
        files: list[tuple[str, bytes]] = []
        total = 0
        for candidate in sorted(canonical_root.rglob("*")):
            if not candidate.is_file():
                continue
            try:
                relative = candidate.relative_to(canonical_root).as_posix()
                if not eligible_relative_path(relative):
                    continue
                if not path_in_scope(relative, included_paths, excluded_paths):
                    continue
                safe = resolve_safe_path(canonical_root, relative)
                size = safe.stat().st_size
                if size > MAX_SOURCE_BYTES:
                    raise ProjectRetrievalError("source_size_limit_exceeded")
                data = safe.read_bytes()
            except OSError as exc:
                raise ProjectRetrievalError("source_read_failed") from exc
            files.append((relative, data))
            total += len(data)
            if len(files) > MAX_FILES:
                raise ProjectRetrievalError("file_count_limit_exceeded")
            if total > MAX_TOTAL_BYTES:
                raise ProjectRetrievalError("corpus_byte_limit_exceeded")
        return files

    def _persist_ingestion(
        self,
        generation: CorpusGeneration,
        source_chunks: list[tuple[CorpusSource, tuple[DocumentChunk, ...], bytes]],
    ) -> None:
        now = generation.created_at.isoformat()
        active_ids = {source.source_id for source, _, _ in source_chunks}
        invalidated_sources = 0
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT source_id FROM rag_sources WHERE project_run_id = ? AND active = 1",
                (generation.project_id,),
            ).fetchall()
            for row in old:
                if row["source_id"] in active_ids:
                    continue
                source_id = str(row["source_id"])
                chunk_rows = connection.execute(
                    "SELECT chunk_id FROM rag_chunks WHERE source_id = ? AND active = 1",
                    (source_id,),
                ).fetchall()
                connection.execute(
                    "UPDATE rag_sources SET active = 0, invalidated_at = ? WHERE source_id = ?",
                    (now, source_id),
                )
                connection.execute(
                    "UPDATE rag_chunks SET active = 0, invalidated_at = ? WHERE source_id = ?",
                    (now, source_id),
                )
                self._record_invalidation(
                    connection,
                    project_id=generation.project_id,
                    target_type="source",
                    target_id=source_id,
                    reason="source_changed_deleted_or_excluded",
                    binding_hash=generation.repository_state_hash,
                    created_at=now,
                )
                for chunk_row in chunk_rows:
                    self._record_invalidation(
                        connection,
                        project_id=generation.project_id,
                        target_type="chunk",
                        target_id=str(chunk_row["chunk_id"]),
                        reason="source_changed_deleted_or_excluded",
                        binding_hash=generation.repository_state_hash,
                        created_at=now,
                    )
                invalidated_sources += 1
            for source, chunks, _ in source_chunks:
                connection.execute(
                    "INSERT OR IGNORE INTO rag_sources "
                    "(source_id, project_run_id, relative_path, content_hash, active, "
                    "source_json, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (
                        source.source_id, source.project_id, source.relative_path,
                        source.content_hash, source.model_dump_json(), now,
                    ),
                )
                connection.execute(
                    "UPDATE rag_sources SET active = 1, invalidated_at = NULL WHERE source_id = ?",
                    (source.source_id,),
                )
                for chunk in chunks:
                    connection.execute(
                        "INSERT OR IGNORE INTO rag_chunks "
                        "(chunk_id, source_id, project_run_id, ordinal, text_hash, active, "
                        "chunk_json, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        (
                            chunk.chunk_id, chunk.source_id, chunk.project_id,
                            chunk.ordinal, chunk.normalized_text_hash,
                            chunk.model_dump_json(), now,
                        ),
                    )
                    connection.execute(
                        "UPDATE rag_chunks SET active = 1, invalidated_at = NULL WHERE chunk_id = ?",
                        (chunk.chunk_id,),
                    )
            connection.execute(
                "INSERT INTO rag_corpus_generations "
                "(generation_id, project_run_id, repository_state_hash, generation_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    generation.generation_id, generation.project_id,
                    generation.repository_state_hash, generation.model_dump_json(), now,
                ),
            )
            connection.execute("COMMIT")
        if invalidated_sources:
            self.invalidate_for_binding_change(
                generation.project_id,
                reason="corpus_generation_changed",
                binding_hash=generation.repository_state_hash,
            )
        self._persist_embeddings(
            tuple(chunk for _, chunks, _ in source_chunks for chunk in chunks)
        )

    @staticmethod
    def _record_invalidation(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        target_type: str,
        target_id: str,
        reason: str,
        binding_hash: str,
        created_at: str,
    ) -> None:
        invalidation_id = (
            f"rag-invalidation-"
            f"{content_hash([target_type, target_id, reason, binding_hash])[:24]}"
        )
        connection.execute(
            "INSERT OR IGNORE INTO rag_invalidations "
            "(invalidation_id, project_run_id, target_type, target_id, reason, "
            "binding_hash, invalidation_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                invalidation_id,
                project_id,
                target_type,
                target_id,
                reason,
                binding_hash,
                canonical_json({
                    "invalidation_id": invalidation_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "reason": reason,
                    "binding_hash": binding_hash,
                    "created_at": created_at,
                }),
                created_at,
            ),
        )

    def _persist_embeddings(self, chunks: tuple[DocumentChunk, ...]) -> None:
        now = _now().isoformat()
        for start in range(0, len(chunks), MAX_EMBEDDING_BATCH):
            batch = chunks[start : start + MAX_EMBEDDING_BATCH]
            try:
                vectors = self.embedding.embed(tuple(item.text for item in batch))
            except EmbeddingUnavailable:
                return
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for chunk, vector in zip(batch, vectors):
                    vector_payload = tuple(round(float(value), 12) for value in vector)
                    connection.execute(
                        "INSERT OR IGNORE INTO rag_embeddings "
                        "(chunk_id, model_identity, model_version, embedding_policy_version, "
                        "chunk_text_hash, dimensions, embedding_hash, vector_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            chunk.chunk_id, self.embedding.identity, self.embedding.version,
                            EMBEDDING_POLICY_VERSION, chunk.normalized_text_hash,
                            len(vector_payload), content_hash(vector_payload),
                            canonical_json(vector_payload), now,
                        ),
                    )
                connection.execute("COMMIT")

    def _active_chunks(self, request: RetrievalRequest) -> tuple[DocumentChunk, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT c.chunk_json FROM rag_chunks c JOIN rag_sources s "
                "ON s.source_id = c.source_id WHERE c.project_run_id = ? "
                "AND c.active = 1 AND s.active = 1 ORDER BY c.chunk_id LIMIT ?",
                (request.project_id, MAX_PROJECT_CHUNKS),
            ).fetchall()
        chunks = tuple(DocumentChunk.model_validate_json(row["chunk_json"]) for row in rows)
        return tuple(
            item for item in chunks
            if item.chunking_policy_version == CHUNKING_POLICY_VERSION
        )

    def _embedding_vectors(
        self, chunks: tuple[DocumentChunk, ...]
    ) -> dict[str, tuple[float, ...]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT chunk_id, chunk_text_hash, vector_json FROM rag_embeddings "
                "WHERE model_identity = ? AND model_version = ? "
                "AND embedding_policy_version = ?",
                (self.embedding.identity, self.embedding.version, EMBEDDING_POLICY_VERSION),
            ).fetchall()
        by_id = {
            row["chunk_id"]: (row["chunk_text_hash"], tuple(json.loads(row["vector_json"])))
            for row in rows
        }
        result: dict[str, tuple[float, ...]] = {}
        for chunk in chunks:
            stored = by_id.get(chunk.chunk_id)
            if stored is None or stored[0] != chunk.normalized_text_hash:
                raise EmbeddingUnavailable("embedding_missing_or_stale")
            result[chunk.chunk_id] = stored[1]
        return result

    @staticmethod
    def _evidence_item(
        chunk: DocumentChunk,
        candidate: RetrievalCandidate,
        rerank_score: float,
        rank: int,
    ) -> RetrievalEvidenceItem:
        text = chunk.text[:8_000]
        return RetrievalEvidenceItem(
            evidence_id=f"rag-evidence-{content_hash([chunk.chunk_id, rank, chunk.normalized_text_hash])[:24]}",
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            relative_path=chunk.relative_path,
            line_start=chunk.start_line,
            line_end=chunk.end_line,
            text=text,
            text_hash=content_hash(text),
            source_content_hash=chunk.source_content_hash,
            bm25_score=candidate.bm25_score,
            semantic_score=candidate.semantic_score,
            hybrid_score=candidate.hybrid_score,
            rerank_score=rerank_score,
            final_rank=rank,
            freshness_status=FreshnessStatus.FRESH,
            binding_status=BindingStatus.EXACT,
            citation_label=f"RAG-{rank}",
        )

    def _claim_request(self, request: RetrievalRequest, fingerprint: str) -> None:
        with self.store.connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO rag_retrieval_requests "
                    "(request_id, project_run_id, idempotency_key, request_fingerprint, "
                    "status, request_json, created_at) VALUES (?, ?, ?, ?, 'started', ?, ?)",
                    (
                        request.request_id, request.project_id, request.idempotency_key,
                        fingerprint, request.model_dump_json(), request.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectRetrievalError("retrieval_request_conflict") from exc

    def _complete_request(
        self,
        request: RetrievalRequest,
        fingerprint: str,
        candidates: tuple[RetrievalCandidate, ...],
        artifact: RetrievalEvidenceArtifact,
    ) -> None:
        now = _now().isoformat()
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                connection.execute(
                    "INSERT INTO rag_retrieval_candidates "
                    "(request_id, chunk_id, rank, candidate_json) VALUES (?, ?, ?, ?)",
                    (
                        request.request_id, candidate.chunk_id,
                        candidate.rank_before_rerank, candidate.model_dump_json(),
                    ),
                )
            connection.execute(
                "INSERT INTO rag_retrieval_artifacts "
                "(artifact_id, project_run_id, request_id, canonical_artifact_id, "
                "artifact_hash, artifact_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id, request.project_id, request.request_id,
                    artifact.artifact_id, artifact.artifact_hash,
                    artifact.model_dump_json(), artifact.created_at.isoformat(),
                ),
            )
            for item in artifact.evidence:
                connection.execute(
                    "INSERT INTO rag_retrieval_evidence "
                    "(artifact_id, evidence_id, chunk_id, final_rank, evidence_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        artifact.artifact_id, item.evidence_id, item.chunk_id,
                        item.final_rank, item.model_dump_json(),
                    ),
                )
            connection.execute(
                "INSERT INTO rag_retrieval_replays "
                "(idempotency_key, project_run_id, request_fingerprint, artifact_id, "
                "replay_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    request.idempotency_key, request.project_id, fingerprint,
                    artifact.artifact_id, artifact.model_dump_json(), now,
                ),
            )
            cursor = connection.execute(
                "UPDATE rag_retrieval_requests SET status = 'completed', artifact_id = ?, "
                "completed_at = ? WHERE request_id = ? AND status = 'started'",
                (artifact.artifact_id, now, request.request_id),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ProjectRetrievalError("retrieval_completion_conflict")
            connection.execute("COMMIT")

    def _fail_request(self, request_id: str) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE rag_retrieval_requests SET status = 'failed', completed_at = ? "
                "WHERE request_id = ? AND status = 'started'",
                (_now().isoformat(), request_id),
            )

    def _replay(
        self, request: RetrievalRequest, fingerprint: str
    ) -> RetrievalEvidenceArtifact | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT request_fingerprint, artifact_id, replay_json "
                "FROM rag_retrieval_replays WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != fingerprint:
            raise ProjectRetrievalError("replay_mismatch")
        if self._artifact_invalidated(row["artifact_id"]):
            raise ProjectRetrievalError("artifact_invalidated")
        artifact = RetrievalEvidenceArtifact.model_validate_json(row["replay_json"])
        canonical = self.artifacts.verify(
            artifact.artifact_id,
            expected_content_hash=artifact.artifact_hash,
        )
        if canonical.artifact_type != ProjectArtifactType.RETRIEVAL_EVIDENCE:
            raise ProjectRetrievalError("retrieval_artifact_type_mismatch")
        self._audit("rag_retrieval_replayed", request.project_id, {
            "request_id": request.request_id, "artifact_id": artifact.artifact_id,
        })
        return artifact.model_copy(update={"replayed": True})

    def _artifact_invalidated(self, artifact_id: str) -> bool:
        with self.store.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM rag_invalidations WHERE target_type = 'artifact' "
                "AND target_id = ? LIMIT 1",
                (artifact_id,),
            ).fetchone() is not None

    def _audit(self, event_type: str, aggregate_id: str, metadata: dict[str, Any]) -> None:
        safe = {
            str(key): value for key, value in metadata.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO local_ai_audit_events "
                "(event_id, event_type, aggregate_id, event_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"rag-event-{uuid4().hex}", event_type, aggregate_id,
                    canonical_json(safe), _now().isoformat(),
                ),
            )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _media_type(path: str) -> str:
    suffix = Path(path).suffix.casefold()
    return {
        ".py": "text/x-python", ".json": "application/json",
        ".md": "text/markdown", ".html": "text/html", ".css": "text/css",
        ".xml": "application/xml", ".yaml": "application/yaml", ".yml": "application/yaml",
    }.get(suffix, "text/plain")
