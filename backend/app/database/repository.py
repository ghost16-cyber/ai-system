from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from pathlib import Path

from backend.app.schemas.api import (
    AnalysisHistoryItem,
    ChatConversationDetail,
    ChatConversationSummary,
    ChatRunResponse,
    FeedbackResponse,
    IssueResponse,
    MetricsResponse,
    PatchApplyResponse,
    PatchProposalResponse,
    PatchProposalStatus,
    PatchVerificationResponse,
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS patch_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    finding_ref TEXT NOT NULL,
                    path TEXT NOT NULL,
                    original_file_sha256 TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    replacement TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_file_sha256 TEXT,
                    applied_at TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'not_requested',
                    verification_tool TEXT,
                    verification_exit_code INTEGER,
                    verification_checked_at TEXT,
                    FOREIGN KEY (analysis_id) REFERENCES analyses (analysis_id)
                )
                """
            )
            self._add_column_if_missing(
                connection, "patch_proposals", "updated_file_sha256", "TEXT"
            )
            self._add_column_if_missing(
                connection, "patch_proposals", "applied_at", "TEXT"
            )
            self._add_column_if_missing(
                connection,
                "patch_proposals",
                "verification_status",
                "TEXT NOT NULL DEFAULT 'not_requested'",
            )
            self._add_column_if_missing(
                connection, "patch_proposals", "verification_tool", "TEXT"
            )
            self._add_column_if_missing(
                connection, "patch_proposals", "verification_exit_code", "INTEGER"
            )
            self._add_column_if_missing(
                connection, "patch_proposals", "verification_checked_at", "TEXT"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_patch_proposals_finding_ref
                ON patch_proposals (finding_ref)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_response TEXT NOT NULL,
                    selected_specialist TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    rag_used INTEGER NOT NULL,
                    rag_skip_reason TEXT,
                    rag_context_count INTEGER NOT NULL,
                    runtime_decision TEXT NOT NULL,
                    safety_decision TEXT NOT NULL,
                    used_real_slm INTEGER NOT NULL DEFAULT 0,
                    slm_provider TEXT NOT NULL DEFAULT 'fallback',
                    slm_model TEXT,
                    slm_fallback_reason TEXT,
                    slm_latency_ms INTEGER,
                    memory_used INTEGER NOT NULL DEFAULT 0,
                    memory_summary TEXT,
                    created_at TEXT NOT NULL,
                    trace_summary_json TEXT NOT NULL
                )
                """
            )
            self._add_column_if_missing(
                connection, "chat_runs", "used_real_slm", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(connection, "chat_runs", "rag_skip_reason", "TEXT")
            self._add_column_if_missing(
                connection, "chat_runs", "slm_provider", "TEXT NOT NULL DEFAULT 'fallback'"
            )
            self._add_column_if_missing(connection, "chat_runs", "slm_model", "TEXT")
            self._add_column_if_missing(connection, "chat_runs", "slm_fallback_reason", "TEXT")
            self._add_column_if_missing(connection, "chat_runs", "slm_latency_ms", "INTEGER")
            self._add_column_if_missing(
                connection, "chat_runs", "memory_used", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(connection, "chat_runs", "memory_summary", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_runs_created_at
                ON chat_runs (created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_runs_conversation
                ON chat_runs (conversation_id, created_at ASC)
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

    def store_chat_run(self, run: ChatRunResponse) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_runs (
                    run_id,
                    conversation_id,
                    user_message,
                    assistant_response,
                    selected_specialist,
                    intent,
                    confidence,
                    rag_used,
                    rag_skip_reason,
                    rag_context_count,
                    runtime_decision,
                    safety_decision,
                    used_real_slm,
                    slm_provider,
                    slm_model,
                    slm_fallback_reason,
                    slm_latency_ms,
                    memory_used,
                    memory_summary,
                    created_at,
                    trace_summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.conversation_id,
                    run.user_message,
                    run.assistant_response,
                    run.selected_specialist,
                    run.intent,
                    run.confidence,
                    int(run.rag_used),
                    run.rag_skip_reason,
                    run.rag_context_count,
                    run.runtime_decision,
                    run.safety_decision,
                    int(run.used_real_slm),
                    run.slm_provider,
                    run.slm_model,
                    run.slm_fallback_reason,
                    run.slm_latency_ms,
                    int(run.memory_used),
                    run.memory_summary,
                    run.created_at.isoformat(),
                    json.dumps(run.trace_summary, sort_keys=True),
                ),
            )

    def list_chat_runs(self, *, limit: int) -> list[ChatRunResponse]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM chat_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._chat_run_from_row(row) for row in rows]

    def list_chat_runs_for_conversation(
        self,
        conversation_id: str,
        *,
        limit: int | None = None,
    ) -> list[ChatRunResponse]:
        query = """
            SELECT *
            FROM chat_runs
            WHERE conversation_id = ?
            ORDER BY created_at ASC
        """
        params: tuple[object, ...] = (conversation_id,)
        if limit is not None:
            query += " LIMIT ?"
            params = (conversation_id, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._chat_run_from_row(row) for row in rows]

    def list_chat_conversations(self, *, limit: int) -> list[ChatConversationSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY conversation_id
                            ORDER BY created_at ASC
                        ) AS first_rank,
                        ROW_NUMBER() OVER (
                            PARTITION BY conversation_id
                            ORDER BY created_at DESC
                        ) AS latest_rank,
                        COUNT(*) OVER (PARTITION BY conversation_id) AS turn_count
                    FROM chat_runs
                )
                SELECT
                    latest.conversation_id,
                    first.user_message AS first_user_message,
                    latest.turn_count,
                    latest.created_at AS latest_timestamp,
                    latest.selected_specialist AS latest_specialist,
                    latest.rag_used AS latest_rag_used,
                    latest.rag_skip_reason AS latest_rag_skip_reason,
                    latest.safety_decision AS latest_safety_decision,
                    latest.runtime_decision AS latest_runtime_decision,
                    latest.memory_summary AS memory_summary
                FROM ranked latest
                JOIN ranked first
                    ON first.conversation_id = latest.conversation_id
                    AND first.first_rank = 1
                WHERE latest.latest_rank = 1
                ORDER BY latest.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            ChatConversationSummary(
                conversation_id=str(row["conversation_id"]),
                title=_conversation_title(str(row["first_user_message"])),
                first_user_message=str(row["first_user_message"]),
                turn_count=int(row["turn_count"]),
                latest_timestamp=row["latest_timestamp"],
                latest_specialist=str(row["latest_specialist"]),
                latest_rag_used=bool(row["latest_rag_used"]),
                latest_rag_skip_reason=(
                    str(row["latest_rag_skip_reason"])
                    if row["latest_rag_skip_reason"]
                    else None
                ),
                latest_safety_decision=str(row["latest_safety_decision"]),
                latest_runtime_decision=str(row["latest_runtime_decision"]),
                memory_summary=str(row["memory_summary"]) if row["memory_summary"] else None,
            )
            for row in rows
        ]

    def get_chat_conversation(self, conversation_id: str) -> ChatConversationDetail:
        turns = self.list_chat_runs_for_conversation(conversation_id)
        if not turns:
            raise LookupError("Conversation not found.")
        latest_summary = next(
            (turn.memory_summary for turn in reversed(turns) if turn.memory_summary),
            None,
        )
        return ChatConversationDetail(
            conversation_id=conversation_id,
            title=_conversation_title(turns[0].user_message),
            memory_summary=latest_summary,
            turns=turns,
        )

    def delete_chat_conversation(self, conversation_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_runs WHERE conversation_id = ?",
                (conversation_id,),
            )
        return int(cursor.rowcount)

    def _chat_run_from_row(self, row: sqlite3.Row) -> ChatRunResponse:
        return ChatRunResponse(
            run_id=str(row["run_id"]),
            conversation_id=str(row["conversation_id"]),
            user_message=str(row["user_message"]),
            assistant_response=str(row["assistant_response"]),
            selected_specialist=str(row["selected_specialist"]),
            intent=str(row["intent"]),
            confidence=float(row["confidence"]),
            rag_used=bool(row["rag_used"]),
            rag_skip_reason=str(row["rag_skip_reason"]) if row["rag_skip_reason"] else None,
            rag_context_count=int(row["rag_context_count"]),
            runtime_decision=str(row["runtime_decision"]),
            safety_decision=str(row["safety_decision"]),
            used_real_slm=bool(row["used_real_slm"]),
            slm_provider=str(row["slm_provider"]),
            slm_model=str(row["slm_model"]) if row["slm_model"] else None,
            slm_fallback_reason=(
                str(row["slm_fallback_reason"])
                if row["slm_fallback_reason"]
                else None
            ),
            slm_latency_ms=(
                int(row["slm_latency_ms"])
                if row["slm_latency_ms"] is not None
                else None
            ),
            memory_used=bool(row["memory_used"]),
            memory_summary=str(row["memory_summary"]) if row["memory_summary"] else None,
            created_at=row["created_at"],
            trace_summary=json.loads(row["trace_summary_json"]),
        )

    def store_patch_proposals(self, proposals: list[PatchProposalResponse]) -> None:
        if not proposals:
            return

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO patch_proposals (
                    proposal_id,
                    analysis_id,
                    finding_ref,
                    path,
                    original_file_sha256,
                    start_line,
                    end_line,
                    replacement,
                    validation_status,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        proposal.proposal_id,
                        proposal.analysis_id,
                        proposal.finding_id,
                        proposal.path,
                        proposal.original_file_sha256,
                        proposal.start_line,
                        proposal.end_line,
                        proposal.replacement,
                        proposal.validation_status,
                        proposal.status,
                    )
                    for proposal in proposals
                ],
            )

    def get_patch_proposal(self, proposal_id: str) -> PatchProposalResponse:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    proposal_id,
                    analysis_id,
                    finding_ref AS finding_id,
                    path,
                    original_file_sha256,
                    start_line,
                    end_line,
                    replacement,
                    validation_status,
                    status
                FROM patch_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()

        if row is None:
            raise LookupError("Patch proposal not found.")

        return PatchProposalResponse(
            proposal_id=row["proposal_id"],
            analysis_id=row["analysis_id"],
            finding_id=row["finding_id"],
            path=row["path"],
            original_file_sha256=row["original_file_sha256"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            replacement=row["replacement"],
            validation_status=row["validation_status"],
            status=row["status"],
        )

    def update_patch_proposal_status(
        self,
        proposal_id: str,
        status: PatchProposalStatus,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE patch_proposals
                SET status = ?
                WHERE proposal_id = ?
                """,
                (status, proposal_id),
            )
        if cursor.rowcount == 0:
            raise LookupError("Patch proposal not found for status update.")

    def record_patch_application(
        self,
        result: PatchApplyResponse,
        *,
        applied_at: datetime,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE patch_proposals
                SET
                    status = ?,
                    updated_file_sha256 = ?,
                    applied_at = ?
                WHERE proposal_id = ?
                """,
                (
                    result.status,
                    result.updated_file_sha256,
                    applied_at.isoformat(),
                    result.proposal_id,
                ),
            )
        if cursor.rowcount == 0:
            raise LookupError("Patch proposal not found for application update.")

    def record_patch_verification(
        self,
        proposal_id: str,
        verification: PatchVerificationResponse,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE patch_proposals
                SET
                    verification_status = ?,
                    verification_tool = ?,
                    verification_exit_code = ?,
                    verification_checked_at = ?
                WHERE proposal_id = ?
                """,
                (
                    verification.status,
                    verification.tool,
                    verification.exit_code,
                    (
                        verification.checked_at.isoformat()
                        if verification.checked_at is not None
                        else None
                    ),
                    proposal_id,
                ),
            )
        if cursor.rowcount == 0:
            raise LookupError("Patch proposal not found for verification update.")

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
            fixes_by_rule = self._count_validated_fixes_by_rule(connection)
            fixable_findings_row = connection.execute(
                """
                SELECT COUNT(*) AS fixable_findings
                FROM findings
                WHERE validation_status IN ('passed', 'failed')
                """
            ).fetchone()
            feedback_totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_feedback,
                    COALESCE(SUM(CASE WHEN helpful = 1 THEN 1 ELSE 0 END), 0) AS helpful_feedback,
                    COALESCE(SUM(CASE WHEN helpful = 0 THEN 1 ELSE 0 END), 0) AS unhelpful_feedback,
                    COALESCE(SUM(CASE WHEN suggestion_accepted = 1 THEN 1 ELSE 0 END), 0) AS accepted_suggestions,
                    COALESCE(SUM(CASE WHEN suggestion_accepted = 0 THEN 1 ELSE 0 END), 0) AS rejected_suggestions
                FROM feedback
                """            ).fetchone()


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
            fixable_findings=int(fixable_findings_row["fixable_findings"]),
            validated_fix_rate=(
                round(int(analysis_totals["validated_fixes"]) / total_findings, 2) 
                if total_findings 
                else 0.0
            ),
            findings_without_fix=(total_findings - int(analysis_totals["validated_fixes"])),
            fixes_by_rule=fixes_by_rule,
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

    def _count_validated_fixes_by_rule(self, connection: sqlite3.Connection) -> dict[str, int]:
        rows = connection.execute(
            """
            SELECT rule_id, COUNT(*) AS fix_count
            FROM findings
            WHERE validation_status = 'passed'
            GROUP BY rule_id
            ORDER BY fix_count DESC, rule_id ASC
            """
        ).fetchall()
        return {str(row["rule_id"]): int(row["fix_count"]) for row in rows}    

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


def _conversation_title(first_user_message: str) -> str:
    title = " ".join(first_user_message.strip().split())
    if not title:
        return "Untitled conversation"
    return title[:80]
