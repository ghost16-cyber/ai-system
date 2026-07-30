from __future__ import annotations

from typing import Protocol

from backend.app.local_ai.contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    EvaluationDefinition,
    EvaluationResult,
    RetrievalRequest,
    RetrievalResult,
    TrainingDefinition,
    TrainingRun,
)


class ArchiveValidator(Protocol):
    def inspect_members(self, source_id: str) -> tuple[dict[str, object], ...]: ...


class SourceFilter(Protocol):
    def classify(self, relative_path: str, license_id: str | None) -> dict[str, object]: ...


class SecretScanner(Protocol):
    def scan_hash(self, content_hash: str) -> dict[str, object]: ...


class ChunkingBackend(Protocol):
    def chunk(self, document_hash: str) -> tuple[dict[str, object], ...]: ...


class EmbeddingBackend(Protocol):
    def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...


class RetrievalBackend(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class EvaluationBackend(Protocol):
    def evaluate(self, definition: EvaluationDefinition) -> EvaluationResult: ...


class TrainingBackend(Protocol):
    def start(self, definition: TrainingDefinition) -> TrainingRun: ...
    def cancel(self, training_run_id: str) -> TrainingRun: ...


class DisabledEmbeddingBackend:
    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        return EmbeddingResult(
            request_id=request.request_id,
            status="intentionally_disabled",
            vector_count=0,
        )


class DisabledRetrievalBackend:
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        return RetrievalResult(
            request_id=request.request_id,
            citations=(),
            poisoned_content_warning=False,
            stale_index=True,
        )


class FakeTrainingBackend:
    """Deterministic contract test backend; it never starts a training process."""

    def start(self, definition: TrainingDefinition) -> TrainingRun:
        return TrainingRun(
            training_run_id=f"fake-{definition.training_definition_id}",
            training_definition_id=definition.training_definition_id,
            status="disabled",
        )

    def cancel(self, training_run_id: str) -> TrainingRun:
        return TrainingRun(
            training_run_id=training_run_id,
            training_definition_id="fake-disabled",
            status="cancelled",
        )


__all__ = [
    "ArchiveValidator", "SourceFilter", "SecretScanner", "ChunkingBackend",
    "EmbeddingBackend", "RetrievalBackend", "EvaluationBackend", "TrainingBackend",
    "DisabledEmbeddingBackend", "DisabledRetrievalBackend", "FakeTrainingBackend",
]
