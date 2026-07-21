from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.database.migrations import apply_schema_migrations, assert_schema_compatible
from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control.contracts import content_hash
from backend.app.project_repair.contracts import (
    DiagnosisArtifact,
    FailureEvidenceArtifact,
    RepairCycle,
    RepairCycleStatus,
    RepairPreviewArtifact,
)


class CanonicalRepairServiceError(RuntimeError):
    pass


class CanonicalRepairService:
    """Stores one bounded repair cycle without owning lifecycle transitions."""

    def __init__(self, database_path: str | Path, control, artifacts: ProjectArtifactStore) -> None:
        self.database_path = Path(database_path)
        self.control = control
        self.artifacts = artifacts

    def initialize(self) -> None:
        apply_schema_migrations(self.database_path)
        assert_schema_compatible(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def capture_failure(
        self,
        *,
        project_run_id: str,
        execution_attempt_id: str,
        failure_classification: str,
        result_reference: dict[str, Any],
        authority: dict[str, Any],
        plan_revision_id: str,
        scope_revision_id: str,
        manifest_hash: str,
    ):
        payload = FailureEvidenceArtifact(
            project_run_id=project_run_id,
            execution_attempt_id=execution_attempt_id,
            failure_classification=failure_classification,
            domain_failure=True,
            result_reference=result_reference,
            authority=authority,
        )
        return self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.FAILURE_EVIDENCE,
            binding=ProjectArtifactBinding(
                project_run_id=project_run_id,
                plan_revision_id=plan_revision_id,
                scope_revision_id=scope_revision_id,
                manifest_hash=manifest_hash,
                execution_attempt_id=execution_attempt_id,
                authority_hash=content_hash(authority),
            ),
            payload=payload.model_dump(mode="json"),
        ))

    def begin_diagnosis(
        self,
        *,
        project_run_id: str,
        failure_artifact_id: str,
        work_unit_id: str,
    ) -> RepairCycle:
        run = self.control.get_project(project_run_id)
        failure = self.artifacts.verify(failure_artifact_id)
        if failure.artifact_type != ProjectArtifactType.FAILURE_EVIDENCE:
            raise CanonicalRepairServiceError("Repair requires a failure-evidence artifact.")
        if failure.binding.project_run_id != project_run_id:
            raise CanonicalRepairServiceError("Failure evidence belongs to another project.")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT cycle_json FROM project_repair_cycles_v2 "
                "WHERE project_run_id = ? AND failure_artifact_id = ?",
                (project_run_id, failure_artifact_id),
            ).fetchone()
            if existing is not None:
                return RepairCycle.model_validate_json(str(existing["cycle_json"]))
            count = int(connection.execute(
                "SELECT COUNT(*) FROM project_repair_cycles_v2 WHERE project_run_id = ?",
                (project_run_id,),
            ).fetchone()[0])
            if count >= 1:
                raise CanonicalRepairServiceError(
                    "The canonical automatic repair budget has been exhausted."
                )
            now = datetime.now(timezone.utc)
            cycle = RepairCycle(
                repair_cycle_id=f"repair-{content_hash([project_run_id, failure_artifact_id])[:24]}",
                project_run_id=project_run_id,
                cycle_number=1,
                plan_revision_id=str(run.current_plan_revision_id or ""),
                scope_revision_id=str(run.current_scope_revision_id or ""),
                manifest_hash=str(run.current_manifest_hash or ""),
                work_unit_id=work_unit_id,
                failure_artifact_id=failure_artifact_id,
                status=RepairCycleStatus.DIAGNOSIS_PENDING,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                "INSERT INTO project_repair_cycles_v2 (repair_cycle_id, project_run_id, "
                "cycle_number, status, failure_artifact_id, schema_version, cycle_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cycle.repair_cycle_id, cycle.project_run_id, cycle.cycle_number,
                    cycle.status.value, cycle.failure_artifact_id, cycle.schema_version,
                    cycle.model_dump_json(), now.isoformat(), now.isoformat(),
                ),
            )
        return cycle

    def record_diagnosis(
        self,
        cycle: RepairCycle,
        *,
        coordinator_intent_id: str,
        root_causes: tuple[dict[str, Any], ...],
        repair_scope: tuple[str, ...],
        strategy: str = "deterministic",
        confidence: str = "high",
        uncertainty_codes: tuple[str, ...] = (),
    ):
        payload = DiagnosisArtifact(
            repair_cycle_id=cycle.repair_cycle_id,
            failure_artifact_id=cycle.failure_artifact_id,
            strategy=strategy,
            root_causes=root_causes,
            repair_scope=repair_scope,
            confidence=confidence,
            uncertainty_codes=uncertainty_codes,
        )
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.DIAGNOSIS,
            binding=self._binding(cycle, coordinator_intent_id),
            payload=payload.model_dump(mode="json"),
            evidence_references=({"artifact_id": cycle.failure_artifact_id},),
        ))
        self._update(
            cycle,
            status=RepairCycleStatus.DIAGNOSED,
            diagnosis_artifact_id=artifact.artifact_id,
        )
        return artifact

    def record_preview(
        self,
        cycle: RepairCycle,
        *,
        coordinator_intent_id: str,
        diagnosis_artifact_id: str,
        patch_id: str,
        operations: tuple[dict[str, Any], ...],
    ):
        payload = RepairPreviewArtifact(
            repair_cycle_id=cycle.repair_cycle_id,
            diagnosis_artifact_id=diagnosis_artifact_id,
            patch_id=patch_id,
            operations=operations,
        )
        artifact = self.artifacts.put(build_project_artifact(
            artifact_type=ProjectArtifactType.REPAIR_PREVIEW,
            binding=self._binding(cycle, coordinator_intent_id),
            payload=payload.model_dump(mode="json"),
            evidence_references=(
                {"artifact_id": cycle.failure_artifact_id},
                {"artifact_id": diagnosis_artifact_id},
            ),
        ))
        self._update(
            cycle,
            status=RepairCycleStatus.PREVIEW_READY,
            diagnosis_artifact_id=diagnosis_artifact_id,
            repair_preview_artifact_id=artifact.artifact_id,
        )
        return artifact

    def finish_cycle(
        self,
        repair_cycle_id: str,
        *,
        status: RepairCycleStatus,
    ) -> RepairCycle:
        cycle = self.get(repair_cycle_id)
        if cycle is None:
            raise CanonicalRepairServiceError("Repair cycle does not exist.")
        return self._update(cycle, status=status, terminal=True)

    def get(self, repair_cycle_id: str) -> RepairCycle | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cycle_json FROM project_repair_cycles_v2 WHERE repair_cycle_id = ?",
                (repair_cycle_id,),
            ).fetchone()
        return RepairCycle.model_validate_json(str(row["cycle_json"])) if row else None

    def list_for_project(self, project_run_id: str) -> list[RepairCycle]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT cycle_json FROM project_repair_cycles_v2 "
                "WHERE project_run_id = ? ORDER BY cycle_number",
                (project_run_id,),
            ).fetchall()
        return [RepairCycle.model_validate_json(str(row["cycle_json"])) for row in rows]

    @staticmethod
    def _binding(cycle: RepairCycle, coordinator_intent_id: str) -> ProjectArtifactBinding:
        return ProjectArtifactBinding(
            project_run_id=cycle.project_run_id,
            plan_revision_id=cycle.plan_revision_id,
            scope_revision_id=cycle.scope_revision_id,
            manifest_hash=cycle.manifest_hash,
            coordinator_intent_id=coordinator_intent_id,
            authority_hash=content_hash({"repair_cycle_id": cycle.repair_cycle_id}),
        )

    def _update(
        self,
        cycle: RepairCycle,
        *,
        status: RepairCycleStatus,
        diagnosis_artifact_id: str | None = None,
        repair_preview_artifact_id: str | None = None,
        terminal: bool = False,
    ) -> RepairCycle:
        now = datetime.now(timezone.utc)
        updated = cycle.model_copy(update={
            "status": status,
            "diagnosis_artifact_id": diagnosis_artifact_id or cycle.diagnosis_artifact_id,
            "repair_preview_artifact_id": (
                repair_preview_artifact_id or cycle.repair_preview_artifact_id
            ),
            "updated_at": now,
            "completed_at": now if terminal else cycle.completed_at,
        })
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE project_repair_cycles_v2 SET status = ?, diagnosis_artifact_id = ?, "
                "repair_preview_artifact_id = ?, cycle_json = ?, updated_at = ?, "
                "completed_at = ? WHERE repair_cycle_id = ?",
                (
                    updated.status.value, updated.diagnosis_artifact_id,
                    updated.repair_preview_artifact_id, updated.model_dump_json(),
                    now.isoformat(),
                    updated.completed_at.isoformat() if updated.completed_at else None,
                    updated.repair_cycle_id,
                ),
            )
            if cursor.rowcount != 1:
                raise CanonicalRepairServiceError("Repair cycle update was lost.")
        return updated


__all__ = ["CanonicalRepairService", "CanonicalRepairServiceError"]
