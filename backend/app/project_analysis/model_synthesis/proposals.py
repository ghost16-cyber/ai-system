from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.database.migrations import apply_schema_migrations, assert_schema_compatible
from backend.app.project_control.contracts import StrictModel, canonical_json, content_hash


MAX_SYNTHESIS_EVIDENCE_BYTES = 196_608
MAX_SYNTHESIS_PROPOSAL_BYTES = 262_144
PROTECTED_PROJECT_PATHS = frozenset({
    ".git", ".env", ".ssh", "node_modules", ".venv", "venv", "__pycache__",
})
ALLOWED_COMMAND_CATEGORIES = frozenset({
    "pytest", "python_compile", "npm_test", "npm_run_typecheck",
    "npm_run_lint", "npm_run_build", "node_test",
})


class ProposalType(StrEnum):
    CLARIFICATION = "clarification"
    IMPLEMENTATION_PLAN = "implementation_plan"
    PATCH = "patch"
    COMMAND = "command"
    DIAGNOSIS = "diagnosis"


class ProposalLifecycle(StrEnum):
    GENERATED = "generated"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    PREVIEWED = "previewed"
    STALE = "stale"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"


class SemanticValidationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class EvidenceTrust(StrEnum):
    DETERMINISTIC = "deterministic"
    USER_APPROVED = "user_approved"
    REPOSITORY_DATA = "untrusted_repository_data"


class SynthesisEvidenceItem(StrictModel):
    evidence_type: str = Field(min_length=1, max_length=100)
    stable_identity: str = Field(min_length=1, max_length=300)
    source_identity: str = Field(min_length=1, max_length=300)
    content_hash: str = Field(min_length=64, max_length=64)
    content: dict[str, Any]
    provenance: dict[str, str] = Field(default_factory=dict)
    freshness_identity: str = Field(min_length=1, max_length=300)
    trust: EvidenceTrust

    @model_validator(mode="after")
    def validate_content(self) -> "SynthesisEvidenceItem":
        if self.content_hash != content_hash(self.content):
            raise ValueError("evidence content hash does not match exact content")
        if len(canonical_json(self.content).encode("utf-8")) > MAX_SYNTHESIS_EVIDENCE_BYTES:
            raise ValueError("evidence item exceeds the bounded size")
        return self


class SynthesisEvidenceEnvelope(StrictModel):
    schema_version: Literal["astra.project-synthesis.evidence-envelope.v1"] = (
        "astra.project-synthesis.evidence-envelope.v1"
    )
    evidence_envelope_id: str = Field(min_length=1, max_length=200)
    project_run_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=3000)
    scope_revision_id: str = Field(min_length=1, max_length=200)
    plan_revision_id: str | None = Field(default=None, max_length=200)
    repository_manifest_identity: str = Field(min_length=64, max_length=64)
    repository_state_identity: str = Field(min_length=1, max_length=300)
    scan_complete: bool
    scope_resolved: bool
    evidence_items: tuple[SynthesisEvidenceItem, ...] = Field(min_length=1, max_length=32)
    allowed_paths: tuple[str, ...] = Field(default=(), max_length=80)
    protected_paths: tuple[str, ...] = Field(default=tuple(sorted(PROTECTED_PROJECT_PATHS)))
    constraints: tuple[str, ...] = Field(default=(), max_length=40)
    permitted_command_categories: tuple[str, ...] = Field(default=(), max_length=20)
    unanswered_clarifications: tuple[str, ...] = Field(default=(), max_length=12)
    invalidated_evidence_identities: tuple[str, ...] = Field(default=(), max_length=80)
    created_at: datetime
    evidence_hash: str = Field(min_length=64, max_length=64)
    project_rag_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_envelope(self) -> "SynthesisEvidenceEnvelope":
        for path in (*self.allowed_paths, *self.protected_paths):
            _validate_relative_path(path)
        if any(
            item.stable_identity in set(self.invalidated_evidence_identities)
            for item in self.evidence_items
        ):
            raise ValueError("the evidence envelope contains invalidated evidence")
        material = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != content_hash(material):
            raise ValueError("evidence envelope hash does not match exact content")
        if len(canonical_json(material).encode("utf-8")) > MAX_SYNTHESIS_EVIDENCE_BYTES:
            raise ValueError("evidence envelope exceeds the bounded size")
        return self


class ClarificationQuestion(StrictModel):
    question: str = Field(min_length=1, max_length=500)
    reason_required: str = Field(min_length=1, max_length=500)
    blocking: bool
    expected_answer_type: Literal["text", "choice", "path", "boolean", "number"]


class ClarificationProposalOutput(StrictModel):
    schema_version: Literal["astra.project-synthesis.clarification.v1"] = (
        "astra.project-synthesis.clarification.v1"
    )
    proposal_type: Literal["clarification"]
    project_run_id: str
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1, max_length=5)
    summary: str = Field(min_length=1, max_length=1000)


class PlanWorkUnit(StrictModel):
    work_unit_id: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)
    target_files: tuple[str, ...] = Field(default=(), max_length=20)
    affected_symbols: tuple[str, ...] = Field(default=(), max_length=30)
    dependencies: tuple[str, ...] = Field(default=(), max_length=20)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=20)
    validation_steps: tuple[str, ...] = Field(min_length=1, max_length=12)
    risks: tuple[str, ...] = Field(default=(), max_length=12)
    assumptions: tuple[str, ...] = Field(default=(), max_length=12)


class ImplementationPlanProposalOutput(StrictModel):
    schema_version: Literal["astra.project-synthesis.implementation-plan.v1"] = (
        "astra.project-synthesis.implementation-plan.v1"
    )
    proposal_type: Literal["implementation_plan"]
    project_run_id: str
    work_units: tuple[PlanWorkUnit, ...] = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=1, max_length=2000)


class PatchProposalOperation(StrictModel):
    operation: Literal["create", "modify", "delete"]
    path: str
    expected_before_sha256: str = Field(pattern=r"^(?:missing|[0-9a-f]{64})$")
    proposed_content: str | None = Field(default=None, max_length=120_000)
    affected_symbols: tuple[str, ...] = Field(default=(), max_length=30)
    evidence_references: tuple[str, ...] = Field(min_length=1, max_length=30)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_operation(self) -> "PatchProposalOperation":
        _validate_relative_path(self.path)
        if self.operation == "create" and self.expected_before_sha256 != "missing":
            raise ValueError("new files require the explicit missing before-state")
        if self.operation != "create" and self.expected_before_sha256 == "missing":
            raise ValueError("existing-file changes require an exact before-state hash")
        if self.operation == "delete" and self.proposed_content is not None:
            raise ValueError("delete operations cannot contain proposed content")
        if self.operation != "delete" and self.proposed_content is None:
            raise ValueError("create and modify operations require proposed content")
        return self


class PatchProposalOutput(StrictModel):
    schema_version: Literal["astra.project-synthesis.patch-proposal.v1"] = (
        "astra.project-synthesis.patch-proposal.v1"
    )
    proposal_type: Literal["patch"]
    project_run_id: str
    summary: str = Field(min_length=1, max_length=2000)
    operations: tuple[PatchProposalOperation, ...] = Field(min_length=1, max_length=10)
    validation_requirements: tuple[str, ...] = Field(min_length=1, max_length=12)
    risk: Literal["low", "medium", "high"]


class CommandProposalOutput(StrictModel):
    schema_version: Literal["astra.project-synthesis.command-proposal.v1"] = (
        "astra.project-synthesis.command-proposal.v1"
    )
    proposal_type: Literal["command"]
    project_run_id: str
    command_category: str = Field(min_length=1, max_length=80)
    argv: tuple[str, ...] = Field(min_length=1, max_length=30)
    working_directory_identity: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=1000)
    timeout_seconds: int = Field(ge=1, le=3600)
    required_approval: Literal["command"] = "command"


class DiagnosisProposalOutput(StrictModel):
    schema_version: Literal["astra.project-synthesis.diagnosis-proposal.v1"] = (
        "astra.project-synthesis.diagnosis-proposal.v1"
    )
    proposal_type: Literal["diagnosis"]
    project_run_id: str
    observed_evidence: tuple[str, ...] = Field(min_length=1, max_length=30)
    probable_cause: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_repair_path: tuple[str, ...] = Field(default=(), max_length=20)
    additional_evidence_required: tuple[str, ...] = Field(default=(), max_length=20)


class SynthesisProposal(StrictModel):
    schema_version: Literal["astra.project-synthesis.proposal.v1"] = (
        "astra.project-synthesis.proposal.v1"
    )
    proposal_id: str
    idempotency_key: str
    proposal_type: ProposalType
    project_run_id: str
    project_job_id: str | None = None
    generation_id: str
    generation_request_id: str
    generation_request_fingerprint: str = Field(min_length=64, max_length=64)
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    provider_identity: str
    endpoint_identity: str
    exact_model_tag: str
    evidence_envelope_id: str
    evidence_hash: str = Field(min_length=64, max_length=64)
    repository_manifest_identity: str = Field(min_length=64, max_length=64)
    repository_state_identity: str
    scope_revision_id: str
    plan_revision_id: str | None = None
    prompt_template_version: str
    expected_schema_identity: str
    semantic_validation_status: SemanticValidationStatus
    lifecycle_state: ProposalLifecycle
    content: dict[str, Any]
    safe_rejection_classification: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_integrity(self) -> "SynthesisProposal":
        material = self.model_dump(mode="json", exclude={"proposal_fingerprint"})
        if self.proposal_fingerprint != content_hash(material):
            raise ValueError("proposal fingerprint does not match immutable content")
        if len(canonical_json(self.content).encode("utf-8")) > MAX_SYNTHESIS_PROPOSAL_BYTES:
            raise ValueError("synthesis proposal content exceeds the bounded size")
        accepted = self.semantic_validation_status == SemanticValidationStatus.ACCEPTED
        if accepted != (self.lifecycle_state == ProposalLifecycle.ACCEPTED):
            raise ValueError("proposal validation and initial lifecycle are inconsistent")
        if accepted == bool(self.safe_rejection_classification):
            raise ValueError("rejected proposals require one safe rejection classification")
        return self


class SynthesisProposalStoreError(RuntimeError):
    pass


class SynthesisProposalStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        apply_schema_migrations(self.database_path)
        assert_schema_compatible(self.database_path)

    def put(self, proposal: SynthesisProposal) -> tuple[SynthesisProposal, bool]:
        candidate = SynthesisProposal.model_validate(proposal.model_dump(mode="python"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT proposal_json, generation_request_fingerprint FROM "
                "project_synthesis_proposals WHERE idempotency_key = ?",
                (candidate.idempotency_key,),
            ).fetchone()
            if row is not None:
                if row["generation_request_fingerprint"] != candidate.generation_request_fingerprint:
                    connection.rollback()
                    raise SynthesisProposalStoreError(
                        "synthesis idempotency key is bound to changed evidence or request"
                    )
                stored = SynthesisProposal.model_validate_json(row["proposal_json"])
                if stored.proposal_fingerprint != candidate.proposal_fingerprint:
                    connection.rollback()
                    raise SynthesisProposalStoreError(
                        "synthesis idempotency key is bound to different proposal content"
                    )
                connection.commit()
                return stored, True
            connection.execute(
                "INSERT INTO project_synthesis_proposals "
                "(proposal_id, idempotency_key, proposal_type, project_run_id, "
                "generation_id, generation_request_id, generation_request_fingerprint, "
                "proposal_fingerprint, evidence_envelope_id, evidence_hash, "
                "repository_manifest_identity, scope_revision_id, plan_revision_id, "
                "semantic_validation_status, initial_lifecycle_state, proposal_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.proposal_id, candidate.idempotency_key,
                    candidate.proposal_type.value, candidate.project_run_id,
                    candidate.generation_id, candidate.generation_request_id,
                    candidate.generation_request_fingerprint,
                    candidate.proposal_fingerprint, candidate.evidence_envelope_id,
                    candidate.evidence_hash, candidate.repository_manifest_identity,
                    candidate.scope_revision_id, candidate.plan_revision_id,
                    candidate.semantic_validation_status.value,
                    candidate.lifecycle_state.value, candidate.model_dump_json(),
                    candidate.created_at.isoformat(),
                ),
            )
            self._append_event(
                connection, candidate.proposal_id, candidate.lifecycle_state,
                {"proposal_fingerprint": candidate.proposal_fingerprint},
                candidate.created_at,
            )
            connection.commit()
            return candidate, False

    def get(self, proposal_id: str) -> SynthesisProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT proposal_json FROM project_synthesis_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return SynthesisProposal.model_validate_json(row[0]) if row else None

    def transition(
        self,
        proposal_id: str,
        state: ProposalLifecycle,
        metadata: dict[str, Any],
    ) -> None:
        allowed = {
            ProposalLifecycle.ACCEPTED: {ProposalLifecycle.PREVIEWED, ProposalLifecycle.STALE, ProposalLifecycle.SUPERSEDED, ProposalLifecycle.INVALIDATED},
            ProposalLifecycle.PREVIEWED: {ProposalLifecycle.STALE, ProposalLifecycle.SUPERSEDED, ProposalLifecycle.INVALIDATED},
            ProposalLifecycle.REJECTED: {ProposalLifecycle.SUPERSEDED, ProposalLifecycle.INVALIDATED},
            ProposalLifecycle.STALE: {ProposalLifecycle.SUPERSEDED, ProposalLifecycle.INVALIDATED},
            ProposalLifecycle.SUPERSEDED: set(),
            ProposalLifecycle.INVALIDATED: set(),
            ProposalLifecycle.GENERATED: {ProposalLifecycle.ACCEPTED, ProposalLifecycle.REJECTED},
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.current_lifecycle(proposal_id, connection=connection)
            if state not in allowed[current]:
                connection.rollback()
                raise SynthesisProposalStoreError("invalid synthesis proposal lifecycle transition")
            self._append_event(connection, proposal_id, state, metadata, _now())
            connection.commit()

    def current_lifecycle(
        self, proposal_id: str, *, connection: sqlite3.Connection | None = None
    ) -> ProposalLifecycle:
        owned = connection is None
        target = connection or self._connect()
        try:
            row = target.execute(
                "SELECT lifecycle_state FROM project_synthesis_proposal_events "
                "WHERE proposal_id = ? ORDER BY sequence DESC LIMIT 1",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise SynthesisProposalStoreError("synthesis proposal does not exist")
            return ProposalLifecycle(row[0])
        finally:
            if owned:
                target.close()

    def list_for_project(self, project_run_id: str, limit: int = 100) -> list[SynthesisProposal]:
        bounded = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT proposal_json FROM project_synthesis_proposals "
                "WHERE project_run_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_run_id, bounded),
            ).fetchall()
        return [SynthesisProposal.model_validate_json(row[0]) for row in rows]

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        proposal_id: str,
        state: ProposalLifecycle,
        metadata: dict[str, Any],
        created_at: datetime,
    ) -> None:
        if len(canonical_json(metadata).encode("utf-8")) > 16_384:
            raise SynthesisProposalStoreError("proposal lifecycle metadata exceeds the bound")
        sequence = int(connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM project_synthesis_proposal_events "
            "WHERE proposal_id = ?", (proposal_id,),
        ).fetchone()[0])
        event_id = f"proposal-event-{content_hash([proposal_id, sequence, state.value, metadata])[:24]}"
        connection.execute(
            "INSERT INTO project_synthesis_proposal_events "
            "(event_id, proposal_id, sequence, lifecycle_state, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, proposal_id, sequence, state.value, canonical_json(metadata), created_at.isoformat()),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection


def build_evidence_envelope(
    *,
    project_run_id: str,
    workspace_id: str,
    objective: str,
    scope_revision_id: str,
    plan_revision_id: str | None,
    manifest_hash: str,
    repository_state_identity: str,
    evidence_identity: str,
    evidence_source_identity: str,
    evidence: dict[str, Any],
    allowed_paths: tuple[str, ...],
    scan_complete: bool = True,
    scope_resolved: bool = True,
    constraints: tuple[str, ...] = (),
    created_at: datetime | None = None,
) -> SynthesisEvidenceEnvelope:
    item = SynthesisEvidenceItem(
        evidence_type="canonical_project_evidence",
        stable_identity=evidence_identity,
        source_identity=evidence_source_identity,
        content_hash=content_hash(evidence),
        content=evidence,
        provenance={"producer": "deterministic_astra_backend", "model_derived": "false"},
        freshness_identity=repository_state_identity,
        trust=EvidenceTrust.DETERMINISTIC,
    )
    now = created_at or _now()
    base = {
        "schema_version": "astra.project-synthesis.evidence-envelope.v1",
        "evidence_envelope_id": f"evidence-envelope-{content_hash([project_run_id, evidence_identity, manifest_hash])[:24]}",
        "project_run_id": project_run_id,
        "workspace_id": workspace_id,
        "objective": objective,
        "scope_revision_id": scope_revision_id,
        "plan_revision_id": plan_revision_id,
        "repository_manifest_identity": manifest_hash,
        "repository_state_identity": repository_state_identity,
        "scan_complete": scan_complete,
        "scope_resolved": scope_resolved,
        "evidence_items": (item,),
        "allowed_paths": allowed_paths,
        "constraints": constraints,
        "permitted_command_categories": tuple(sorted(ALLOWED_COMMAND_CATEGORIES)),
        "created_at": now,
        "project_rag_enabled": False,
    }
    draft = SynthesisEvidenceEnvelope.model_construct(
        **base, evidence_hash="0" * 64
    )
    material = draft.model_dump(mode="json", exclude={"evidence_hash"})
    return SynthesisEvidenceEnvelope(**base, evidence_hash=content_hash(material))


def build_synthesis_proposal(**values: Any) -> SynthesisProposal:
    base = dict(values)
    base.setdefault("created_at", _now())
    base.setdefault(
        "proposal_id",
        f"synthesis-proposal-{content_hash([base['project_run_id'], base['idempotency_key']])[:24]}",
    )
    draft = SynthesisProposal.model_construct(
        **base, proposal_fingerprint="0" * 64
    )
    material = draft.model_dump(mode="json", exclude={"proposal_fingerprint"})
    return SynthesisProposal(**base, proposal_fingerprint=content_hash(material))


def validate_plan_semantics(output: ImplementationPlanProposalOutput, envelope: SynthesisEvidenceEnvelope) -> None:
    _validate_project(output.project_run_id, envelope)
    ids = {item.work_unit_id for item in output.work_units}
    if len(ids) != len(output.work_units):
        raise ValueError("plan work-unit identities must be unique")
    if any(dependency not in ids for item in output.work_units for dependency in item.dependencies):
        raise ValueError("plan dependency references an unknown work unit")
    graph = {item.work_unit_id: set(item.dependencies) for item in output.work_units}
    pending = set(graph)
    while pending:
        ready = {item for item in pending if not (graph[item] & pending)}
        if not ready:
            raise ValueError("plan dependency cycle")
        pending -= ready
    for item in output.work_units:
        for path in item.target_files:
            _validate_allowed_path(path, envelope)


def validate_patch_semantics(output: PatchProposalOutput, envelope: SynthesisEvidenceEnvelope) -> None:
    _validate_project(output.project_run_id, envelope)
    for operation in output.operations:
        _validate_allowed_path(operation.path, envelope)
        if operation.operation != "create" and operation.path not in envelope.allowed_paths:
            raise ValueError("patch references an unknown or out-of-scope file")


def validate_command_semantics(output: CommandProposalOutput, envelope: SynthesisEvidenceEnvelope) -> None:
    _validate_project(output.project_run_id, envelope)
    if output.command_category not in ALLOWED_COMMAND_CATEGORIES:
        raise ValueError("command category is not canonically allowed")
    if output.command_category not in set(envelope.permitted_command_categories):
        raise ValueError("command category is outside the evidence envelope")
    if any(re.search(r"(?:&&|\|\||[;<>`]|\$\()", argument) for argument in output.argv):
        raise ValueError("shell composition and redirection are forbidden")
    executable = output.argv[0].lower()
    if executable in {"docker", "podman", "bash", "sh", "powershell", "pwsh", "cmd"}:
        raise ValueError("direct shell or container invocation is forbidden")


def validate_diagnosis_semantics(output: DiagnosisProposalOutput, envelope: SynthesisEvidenceEnvelope) -> None:
    _validate_project(output.project_run_id, envelope)
    evidence_ids = {item.stable_identity for item in envelope.evidence_items}
    if any(identity not in evidence_ids for identity in output.observed_evidence):
        raise ValueError("diagnosis references evidence outside the envelope")
    for path in output.recommended_repair_path:
        _validate_allowed_path(path, envelope)
    if output.confidence > 0.95:
        raise ValueError("unsupported diagnosis certainty")


def validate_clarification_semantics(output: ClarificationProposalOutput, envelope: SynthesisEvidenceEnvelope) -> None:
    _validate_project(output.project_run_id, envelope)
    normalized = [" ".join(item.question.lower().split()) for item in output.questions]
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate clarification questions")


def _validate_project(project_run_id: str, envelope: SynthesisEvidenceEnvelope) -> None:
    if project_run_id != envelope.project_run_id:
        raise ValueError("proposal project identity does not match evidence")
    if not envelope.scan_complete:
        raise ValueError("repository scan is incomplete")
    if not envelope.scope_resolved or envelope.unanswered_clarifications:
        raise ValueError("project scope remains unresolved")


def _validate_allowed_path(path: str, envelope: SynthesisEvidenceEnvelope) -> None:
    _validate_relative_path(path)
    first = PurePosixPath(path).parts[0]
    if first in PROTECTED_PROJECT_PATHS or path in set(envelope.protected_paths):
        raise ValueError("protected project path")
    if path not in set(envelope.allowed_paths):
        raise ValueError("path is outside approved scope")


def _validate_relative_path(path: str) -> None:
    normalized = path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or ".." in candidate.parts
        or candidate.as_posix() != normalized
    ):
        raise ValueError("path must be one normalized project-relative identity")


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ALLOWED_COMMAND_CATEGORIES", "ClarificationProposalOutput",
    "CommandProposalOutput", "DiagnosisProposalOutput", "EvidenceTrust",
    "ImplementationPlanProposalOutput", "PatchProposalOutput", "ProposalLifecycle",
    "ProposalType", "SemanticValidationStatus", "SynthesisEvidenceEnvelope",
    "SynthesisEvidenceItem", "SynthesisProposal", "SynthesisProposalStore",
    "SynthesisProposalStoreError", "build_evidence_envelope",
    "build_synthesis_proposal", "validate_clarification_semantics",
    "validate_command_semantics", "validate_diagnosis_semantics",
    "validate_patch_semantics", "validate_plan_semantics",
]
