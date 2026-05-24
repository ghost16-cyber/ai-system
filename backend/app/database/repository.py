from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from backend.app.schemas.api import (
    AnalysisHistoryItem,
    FeedbackResponse,
    IssueResponse,
    MetricsResponse,
)


class AnalysisRepository:
    """SQLite storage for analysis metadata; submitted source is not persisted."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    code_sha256 TEXT NOT NULL,
                    language TEXT NOT NULL,
                    filename TEXT,
                    code_length INTEGER NOT NULL,
                    line_count INTEGER NOT NULL,
                    issue_count INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL
                )
                """
            )
            self._add_column_if_missing(
                connection, "analyses", "parse_success", "INTEGER NOT NULL DEFAULT 1"
            )
            self._add_column_if_missing(
                connection,
                "analyses",
                "validated_fix_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_ref TEXT,
                    analysis_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    FOREIGN KEY (analysis_id) REFERENCES analyses (analysis_id)
                )
                """
            )
            self._add_column_if_missing(connection, "findings", "finding_ref", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_findings_analysis_id
                ON findings (analysis_id)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_finding_ref
                ON findings (finding_ref)
                WHERE finding_ref IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    finding_ref TEXT NOT NULL,
                    helpful INTEGER NOT NULL,
                    suggestion_accepted INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (analysis_id) REFERENCES analyses (analysis_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_finding_ref
                ON feedback (finding_ref)
                """
            )

    def _add_column_if_missing(
        self, connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def status(self) -> str:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return "ready"

    def store_analysis(
        self,
        *,
        analysis_id: str,
        created_at: datetime,
        code_hash: str,
        language: str,
        filename: str | None,
        code_length: int,
        line_count: int,
        issue_count: int,
        parse_success: bool,
        validated_fix_count: int,
        phase: str,
        findings: list[IssueResponse],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id,
                    created_at,
                    code_sha256,
                    language,
                    filename,
                    code_length,
                    line_count,
                    issue_count,
                    phase,
                    parse_success,
                    validated_fix_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    created_at.isoformat(),
                    code_hash,
                    language,
                    filename,
                    code_length,
                    line_count,
                    issue_count,
                    phase,
                    int(parse_success),
                    validated_fix_count,
                ),
            )
            connection.executemany(
                """
                INSERT INTO findings (
                    finding_ref,
                    analysis_id,
                    rule_id,
                    source,
                    category,
                    severity,
                    validation_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        finding.finding_id,
                        analysis_id,
                        finding.rule_id,
                        finding.source,
                        finding.category,
                        finding.severity,
                        finding.validation.status,
                    )
                    for finding in findings
                ],
            )

    def list_analyses(self, *, limit: int) -> list[AnalysisHistoryItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    analysis_id,
                    created_at,
                    code_sha256,
                    language,
                    filename,
                    code_length,
                    line_count,
                    issue_count,
                    phase
                FROM analyses
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [AnalysisHistoryItem.model_validate(dict(row)) for row in rows]

    def get_metrics(self, *, phase: str) -> MetricsResponse:
        with self._connect() as connection:
            analysis_totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_analyses,
                    COALESCE(SUM(CASE WHEN issue_count > 0 THEN 1 ELSE 0 END), 0)
                        AS analyses_with_findings,
                    COALESCE(SUM(issue_count), 0) AS total_findings,
                    COALESCE(SUM(CASE WHEN parse_success = 0 THEN 1 ELSE 0 END), 0)
                        AS parse_failures,
                    COALESCE(SUM(validated_fix_count), 0) AS validated_fixes
                FROM analyses
                """
            ).fetchone()
            findings_by_rule = self._count_findings_by(connection, "rule_id")
            findings_by_severity = self._count_findings_by(connection, "severity")
            validation_statuses = self._count_findings_by(
                connection, "validation_status"
            )
            feedback_totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_feedback,
                    COALESCE(SUM(CASE WHEN helpful = 1 THEN 1 ELSE 0 END), 0)
                        AS helpful_feedback,
                    COALESCE(SUM(CASE WHEN helpful = 0 THEN 1 ELSE 0 END), 0)
                        AS unhelpful_feedback,
                    COALESCE(SUM(CASE WHEN suggestion_accepted = 1 THEN 1 ELSE 0 END), 0)
                        AS accepted_suggestions,
                    COALESCE(SUM(CASE WHEN suggestion_accepted = 0 THEN 1 ELSE 0 END), 0)
                        AS rejected_suggestions
                FROM feedback
                """
            ).fetchone()

        total_analyses = int(analysis_totals["total_analyses"])
        total_findings = int(analysis_totals["total_findings"])
        analyses_with_findings = int(analysis_totals["analyses_with_findings"])
        accepted_suggestions = int(feedback_totals["accepted_suggestions"])
        rejected_suggestions = int(feedback_totals["rejected_suggestions"])
        suggestion_decisions = accepted_suggestions + rejected_suggestions
        return MetricsResponse(
            phase=phase,
            total_analyses=total_analyses,
            analyses_with_findings=analyses_with_findings,
            clean_analyses=total_analyses - analyses_with_findings,
            total_findings=total_findings,
            average_findings_per_analysis=(
                round(total_findings / total_analyses, 2) if total_analyses else 0.0
            ),
            parse_failures=int(analysis_totals["parse_failures"]),
            validated_fixes=int(analysis_totals["validated_fixes"]),
            findings_by_rule=findings_by_rule,
            findings_by_severity=findings_by_severity,
            validation_statuses=validation_statuses,
            total_feedback=int(feedback_totals["total_feedback"]),
            helpful_feedback=int(feedback_totals["helpful_feedback"]),
            unhelpful_feedback=int(feedback_totals["unhelpful_feedback"]),
            accepted_suggestions=accepted_suggestions,
            rejected_suggestions=rejected_suggestions,
            suggestion_acceptance_rate=(
                round(accepted_suggestions / suggestion_decisions, 2)
                if suggestion_decisions
                else None
            ),
        )

    def _count_findings_by(
        self, connection: sqlite3.Connection, field_name: str
    ) -> dict[str, int]:
        allowed_fields = {"rule_id", "severity", "validation_status"}
        if field_name not in allowed_fields:
            raise ValueError(f"Unsupported finding field: {field_name}")
        rows = connection.execute(
            f"""
            SELECT {field_name}, COUNT(*) AS finding_count
            FROM findings
            GROUP BY {field_name}
            ORDER BY finding_count DESC, {field_name} ASC
            """
        ).fetchall()
        return {str(row[field_name]): int(row["finding_count"]) for row in rows}

    def store_feedback(
        self,
        *,
        feedback_id: str,
        analysis_id: str,
        finding_id: str,
        helpful: bool,
        suggestion_accepted: bool | None,
        created_at: datetime,
    ) -> FeedbackResponse:
        with self._connect() as connection:
            finding = connection.execute(
                """
                SELECT validation_status
                FROM findings
                WHERE analysis_id = ? AND finding_ref = ?
                """,
                (analysis_id, finding_id),
            ).fetchone()
            if finding is None:
                raise LookupError("Finding not found for this analysis.")
            if (
                suggestion_accepted is not None
                and finding["validation_status"] != "passed"
            ):
                raise ValueError(
                    "Acceptance can only be recorded for a validated suggestion."
                )
            connection.execute(
                """
                INSERT INTO feedback (
                    feedback_id,
                    analysis_id,
                    finding_ref,
                    helpful,
                    suggestion_accepted,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(finding_ref) DO UPDATE SET
                    feedback_id = excluded.feedback_id,
                    analysis_id = excluded.analysis_id,
                    helpful = excluded.helpful,
                    suggestion_accepted = excluded.suggestion_accepted,
                    created_at = excluded.created_at
                """,
                (
                    feedback_id,
                    analysis_id,
                    finding_id,
                    int(helpful),
                    (
                        int(suggestion_accepted)
                        if suggestion_accepted is not None
                        else None
                    ),
                    created_at.isoformat(),
                ),
            )

        return FeedbackResponse(
            feedback_id=feedback_id,
            analysis_id=analysis_id,
            finding_id=finding_id,
            helpful=helpful,
            suggestion_accepted=suggestion_accepted,
            created_at=created_at,
        )
