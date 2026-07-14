from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Any

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


PROJECT_JOB_TRANSITIONS = {
    "intake": {"needs_clarification", "planned", "cancelled"},
    "needs_clarification": {"planned", "cancelled"},
    "planned": {"needs_clarification", "patch_proposed", "cancelled"},
    "patch_proposed": {"patch_approved", "planned", "cancelled"},
    "patch_approved": {"implementing", "blocked", "cancelled"},
    "implementing": {"validating", "blocked", "cancelled"},
    "validating": {"completed", "blocked", "implementing", "cancelled"},
    "blocked": {"needs_clarification", "planned", "patch_proposed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


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
                    rag_sources_json TEXT NOT NULL DEFAULT '[]',
                    source_count INTEGER NOT NULL DEFAULT 0,
                    source_paths_json TEXT NOT NULL DEFAULT '[]',
                    grounding_status TEXT NOT NULL DEFAULT 'none',
                    corpus_retrieval_used INTEGER NOT NULL DEFAULT 0,
                    corpus_retrieval_skip_reason TEXT,
                    corpus_context_count INTEGER NOT NULL DEFAULT 0,
                    corpus_sources_json TEXT NOT NULL DEFAULT '[]',
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
                    trace_summary_json TEXT NOT NULL,
                    action_json TEXT
                )
                """
            )
            self._add_column_if_missing(
                connection, "chat_runs", "used_real_slm", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(connection, "chat_runs", "rag_skip_reason", "TEXT")
            self._add_column_if_missing(
                connection, "chat_runs", "rag_sources_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "source_count", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "source_paths_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "grounding_status", "TEXT NOT NULL DEFAULT 'none'"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "corpus_retrieval_used", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "corpus_retrieval_skip_reason", "TEXT"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "corpus_context_count", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(
                connection, "chat_runs", "corpus_sources_json", "TEXT NOT NULL DEFAULT '[]'"
            )
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
            self._add_column_if_missing(connection, "chat_runs", "action_json", "TEXT")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_patches (
                    patch_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    job_id TEXT,
                    status TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._add_column_if_missing(connection, "project_patches", "job_id", "TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_patches_conversation
                ON project_patches (conversation_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_audit_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    patch_id TEXT,
                    job_id TEXT,
                    command_plan_id TEXT,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self._add_column_if_missing(connection, "project_audit_events", "job_id", "TEXT")
            self._add_column_if_missing(connection, "project_audit_events", "command_plan_id", "TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_jobs (
                    job_id TEXT PRIMARY KEY,
                    action_run_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision_count INTEGER NOT NULL DEFAULT 0,
                    job_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_jobs_conversation
                ON project_jobs (conversation_id, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_jobs_folder_access
                ON project_jobs (folder_access_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_analyses (
                    analysis_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    index_version TEXT NOT NULL,
                    index_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_analyses_job
                ON project_analyses (job_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_synthesis_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    analysis_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_hash TEXT,
                    evidence_hash TEXT NOT NULL,
                    patch_id TEXT,
                    attempt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_synthesis_attempts_job
                ON project_synthesis_attempts (job_id, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_failure_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    command_execution_id TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    parent_patch_id TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    project_state_hash TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_failure_evidence_job
                ON project_failure_evidence (job_id, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    failure_evidence_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    analysis_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    strategy TEXT,
                    status TEXT NOT NULL,
                    request_hash TEXT,
                    response_hash TEXT,
                    diagnosis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_diagnoses_failure
                ON project_diagnoses (failure_evidence_id, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_repair_cycles (
                    repair_cycle_id TEXT PRIMARY KEY,
                    repair_chain_id TEXT NOT NULL,
                    cycle_number INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    parent_patch_id TEXT NOT NULL,
                    repair_patch_id TEXT,
                    command_execution_id TEXT NOT NULL UNIQUE,
                    failure_evidence_id TEXT NOT NULL,
                    diagnosis_id TEXT,
                    synthesis_attempt_id TEXT,
                    root_fingerprint TEXT NOT NULL,
                    analysis_id TEXT,
                    status TEXT NOT NULL,
                    cycle_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(repair_chain_id, cycle_number)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_repair_cycles_job
                ON project_repair_cycles (job_id, cycle_number ASC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_delivery_jobs (
                    delivery_job_id TEXT PRIMARY KEY,
                    action_run_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_access_id TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 1,
                    job_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_delivery_conversation
                ON project_delivery_jobs (conversation_id, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_delivery_workspace
                ON project_delivery_jobs (folder_access_id, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_delivery_records (
                    record_id TEXT PRIMARY KEY,
                    delivery_job_id TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    immutable_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(delivery_job_id, record_type, immutable_hash)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_delivery_records_job
                ON project_delivery_records (delivery_job_id, record_type, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_delivery_audit_events (
                    event_id TEXT PRIMARY KEY,
                    delivery_job_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
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
                    rag_sources_json,
                    source_count,
                    source_paths_json,
                    grounding_status,
                    corpus_retrieval_used,
                    corpus_retrieval_skip_reason,
                    corpus_context_count,
                    corpus_sources_json,
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
                    trace_summary_json,
                    action_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(
                        [
                            source.model_dump(mode="json")
                            if hasattr(source, "model_dump")
                            else source
                            for source in run.rag_sources
                        ],
                        sort_keys=True,
                    ),
                    run.source_count,
                    json.dumps(run.source_paths, sort_keys=True),
                    run.grounding_status,
                    int(run.corpus_retrieval_used),
                    run.corpus_retrieval_skip_reason,
                    run.corpus_context_count,
                    json.dumps(
                        [
                            source.model_dump(mode="json")
                            if hasattr(source, "model_dump")
                            else source
                            for source in run.corpus_sources
                        ],
                        sort_keys=True,
                    ),
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
                    json.dumps(run.action, sort_keys=True) if run.action is not None else None,
                ),
            )

    def chat_run_action_matches_plan(
        self,
        run_id: str,
        plan_id: str,
    ) -> bool:
        """Return whether a stored chat action belongs to a command plan."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT action_json
                FROM chat_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        if row is None or not row["action_json"]:
            return False

        try:
            action = json.loads(row["action_json"])
        except (TypeError, json.JSONDecodeError):
            return False

        if not isinstance(action, dict):
            return False

        technical_details = action.get("technical_details")
        if not isinstance(technical_details, dict):
            return False

        command_plan = technical_details.get("command_plan")
        return (
            isinstance(command_plan, dict)
            and command_plan.get("plan_id") == plan_id
        )

    def get_chat_run(self, run_id: str) -> ChatRunResponse:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM chat_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        if row is None:
            raise LookupError("Chat run not found.")
        return self._chat_run_from_row(row)

    def chat_run_action_matches_id(
        self,
        run_id: str,
        action_id: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT action_json
                FROM chat_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        if row is None or not row["action_json"]:
            return False

        try:
            action = json.loads(row["action_json"])
        except (TypeError, json.JSONDecodeError):
            return False

        return isinstance(action, dict) and action.get("action_id") == action_id

    def update_chat_run_action_for_id(
        self,
        run_id: str,
        action_id: str,
        updates: dict,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT action_json
                FROM chat_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if row is None or not row["action_json"]:
                return False

            try:
                action = json.loads(row["action_json"])
            except (TypeError, json.JSONDecodeError):
                return False

            if not isinstance(action, dict) or action.get("action_id") != action_id:
                return False

            merged_action = dict(action)
            technical_details = action.get("technical_details")
            if not isinstance(technical_details, dict):
                technical_details = {}

            technical_updates = updates.get("technical_details")
            if isinstance(technical_updates, dict):
                merged_details = dict(technical_details)
                merged_details.update(technical_updates)
                merged_action["technical_details"] = merged_details

            for key, value in updates.items():
                if key != "technical_details":
                    merged_action[key] = value

            connection.execute(
                """
                UPDATE chat_runs
                SET action_json = ?
                WHERE run_id = ?
                """,
                (json.dumps(merged_action, sort_keys=True), run_id),
            )

        return True

    def update_chat_run_action_for_plan(
        self,
        run_id: str,
        plan_id: str,
        updates: dict,
    ) -> bool:
        """Merge lifecycle updates into a stored chat action.

        The stored action must belong to the supplied command plan. This
        prevents a client from updating an unrelated chat run by providing
        another run ID.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT action_json
                FROM chat_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            if row is None or not row["action_json"]:
                return False

            try:
                action = json.loads(row["action_json"])
            except (TypeError, json.JSONDecodeError):
                return False

            if not isinstance(action, dict):
                return False

            technical_details = action.get("technical_details")
            if not isinstance(technical_details, dict):
                return False

            command_plan = technical_details.get("command_plan")
            if (
                not isinstance(command_plan, dict)
                or command_plan.get("plan_id") != plan_id
            ):
                return False

            merged_action = dict(action)

            technical_updates = updates.get("technical_details")
            if isinstance(technical_updates, dict):
                merged_details = dict(technical_details)
                merged_details.update(technical_updates)
                merged_action["technical_details"] = merged_details

            for key, value in updates.items():
                if key != "technical_details":
                    merged_action[key] = value

            connection.execute(
                """
                UPDATE chat_runs
                SET action_json = ?
                WHERE run_id = ?
                """,
                (json.dumps(merged_action, sort_keys=True), run_id),
            )

        return True

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

    def store_project_patch(self, proposal: dict[str, Any], snapshot: list[dict[str, Any]] | None = None) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_patches (
                    patch_id, conversation_id, folder_access_id, job_id, status,
                    proposal_json, snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal["patch_id"], proposal["conversation_id"], proposal["folder_access_id"],
                    proposal.get("job_id"), proposal["status"], json.dumps(proposal, sort_keys=True),
                    json.dumps(snapshot or [], sort_keys=True), proposal["created_at"], now,
                ),
            )

    def store_project_job(self, job: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_jobs (
                    job_id, action_run_id, conversation_id, folder_access_id,
                    root_fingerprint, status, revision_count, job_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["job_id"], job["action_run_id"], job["conversation_id"],
                    job["folder_access_id"], job["root_fingerprint"], job["status"],
                    int(job.get("revision_count") or 0), json.dumps(job, sort_keys=True),
                    job["created_at"], job["updated_at"],
                ),
            )

    def store_project_delivery_job(self, job: dict[str, Any]) -> None:
        stored = dict(job)
        stored.setdefault("state_version", 1)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_delivery_jobs (
                    delivery_job_id, action_run_id, conversation_id, folder_access_id,
                    root_fingerprint, status, state_version, job_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored["delivery_job_id"], stored["action_run_id"], stored["conversation_id"],
                    stored["folder_access_id"], stored["root_fingerprint"], stored["status"],
                    int(stored["state_version"]), json.dumps(stored, sort_keys=True),
                    stored["created_at"], stored["updated_at"],
                ),
            )

    def get_project_delivery_job(self, delivery_job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_json FROM project_delivery_jobs WHERE delivery_job_id = ?",
                (delivery_job_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project delivery job not found.")
        value = json.loads(row["job_json"])
        if not isinstance(value, dict):
            raise LookupError("Project delivery job state is malformed.")
        return value

    def list_project_delivery_jobs_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_json FROM project_delivery_jobs
                WHERE conversation_id = ? ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        values = [json.loads(row["job_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def latest_active_project_delivery_job(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_json FROM project_delivery_jobs
                WHERE conversation_id = ? AND status NOT IN ('delivery_completed', 'cancelled')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row["job_json"])
        return value if isinstance(value, dict) else None

    def transition_project_delivery_job(
        self,
        job: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any] | None:
        stored = dict(job)
        stored["state_version"] = expected_version + 1
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_delivery_jobs
                SET status = ?, state_version = ?, job_json = ?, updated_at = ?
                WHERE delivery_job_id = ? AND conversation_id = ? AND folder_access_id = ?
                  AND state_version = ?
                """,
                (
                    stored["status"], stored["state_version"], json.dumps(stored, sort_keys=True),
                    stored["updated_at"], stored["delivery_job_id"], stored["conversation_id"],
                    stored["folder_access_id"], expected_version,
                ),
            )
        return stored if cursor.rowcount == 1 else None

    def store_project_delivery_record(
        self,
        *,
        delivery_job_id: str,
        record_type: str,
        immutable_hash: str,
        record: dict[str, Any],
        record_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = str(record.get("created_at") or record.get("verified_at") or datetime.now().astimezone().isoformat())
        identifier = record_id or str(record.get("record_id") or record.get("verification_id") or record.get("handoff_id") or immutable_hash)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_delivery_records (
                    record_id, delivery_job_id, record_type, immutable_hash, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_job_id, record_type, immutable_hash) DO NOTHING
                """,
                (identifier, delivery_job_id, record_type, immutable_hash, json.dumps(record, sort_keys=True), created_at),
            )
            row = connection.execute(
                """
                SELECT record_json FROM project_delivery_records
                WHERE delivery_job_id = ? AND record_type = ? AND immutable_hash = ?
                """,
                (delivery_job_id, record_type, immutable_hash),
            ).fetchone()
        value = json.loads(row["record_json"])
        return value if isinstance(value, dict) else record

    def list_project_delivery_records(self, delivery_job_id: str, record_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT record_json FROM project_delivery_records WHERE delivery_job_id = ?"
        parameters: tuple[Any, ...] = (delivery_job_id,)
        if record_type is not None:
            sql += " AND record_type = ?"
            parameters = (delivery_job_id, record_type)
        sql += " ORDER BY created_at ASC"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        values = [json.loads(row["record_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def store_project_delivery_audit_event(self, event: dict[str, Any]) -> None:
        metadata = json.dumps(event.get("metadata") or {}, sort_keys=True)
        if len(metadata) > 4_000:
            metadata = json.dumps({"bounded": True, "summary": metadata[:3_800]}, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_delivery_audit_events (
                    event_id, delivery_job_id, conversation_id, operation,
                    status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event["event_id"], event["delivery_job_id"], event["conversation_id"],
                    event["operation"], event["status"], metadata, event["created_at"],
                ),
            )

    def list_project_delivery_audit_events(self, delivery_job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM project_delivery_audit_events
                WHERE delivery_job_id = ? ORDER BY created_at ASC
                """,
                (delivery_job_id,),
            ).fetchall()
        return [{
            "event_id": row["event_id"], "delivery_job_id": row["delivery_job_id"],
            "conversation_id": row["conversation_id"], "operation": row["operation"],
            "status": row["status"], "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        } for row in rows]

    def get_project_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_json FROM project_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project job not found.")
        job = json.loads(row["job_json"])
        if not isinstance(job, dict):
            raise LookupError("Project job state is malformed.")
        return job

    def list_project_jobs_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_json FROM project_jobs
                WHERE conversation_id = ? ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        jobs = [json.loads(row["job_json"]) for row in rows]
        return [job for job in jobs if isinstance(job, dict)]

    def latest_active_project_job(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_json FROM project_jobs
                WHERE conversation_id = ? AND status NOT IN ('completed', 'cancelled')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        job = json.loads(row["job_json"])
        return job if isinstance(job, dict) else None

    def update_project_job(self, job: dict[str, Any]) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_jobs
                SET status = ?, revision_count = ?, job_json = ?, updated_at = ?
                WHERE job_id = ? AND conversation_id = ? AND folder_access_id = ?
                """,
                (
                    job["status"], int(job.get("revision_count") or 0),
                    json.dumps(job, sort_keys=True), job["updated_at"], job["job_id"],
                    job["conversation_id"], job["folder_access_id"],
                ),
            )
        if cursor.rowcount != 1:
            raise LookupError("Project job update failed or its security binding changed.")

    def transition_project_job(
        self,
        job: dict[str, Any],
        *,
        expected_statuses: set[str] | tuple[str, ...] | list[str],
    ) -> bool:
        expected = tuple(expected_statuses)
        if not expected:
            return False
        target = str(job.get("status") or "")
        if target not in PROJECT_JOB_TRANSITIONS:
            raise ValueError("Unknown project job status.")
        if any(target not in PROJECT_JOB_TRANSITIONS.get(status, set()) for status in expected):
            raise ValueError("Invalid project job status transition.")
        placeholders = ", ".join("?" for _ in expected)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE project_jobs
                SET status = ?, revision_count = ?, job_json = ?, updated_at = ?
                WHERE job_id = ? AND conversation_id = ? AND folder_access_id = ?
                  AND status IN ({placeholders})
                """,
                (
                    job["status"], int(job.get("revision_count") or 0),
                    json.dumps(job, sort_keys=True), job["updated_at"], job["job_id"],
                    job["conversation_id"], job["folder_access_id"], *expected,
                ),
            )
        return cursor.rowcount == 1

    def list_project_patches_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal_json FROM project_patches
                WHERE job_id = ? ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        patches = [json.loads(row["proposal_json"]) for row in rows]
        return [patch for patch in patches if isinstance(patch, dict)]

    def store_project_analysis(self, index: dict[str, Any]) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_analyses (
                    analysis_id, job_id, conversation_id, folder_access_id,
                    root_fingerprint, index_version, index_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index["analysis_id"], index["job_id"], index["conversation_id"],
                    index["folder_access_id"], index["root_fingerprint"], index["index_version"],
                    json.dumps(index, sort_keys=True), index["created_at"], now,
                ),
            )

    def get_project_analysis(self, analysis_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT index_json FROM project_analyses WHERE analysis_id = ?", (analysis_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project analysis not found.")
        index = json.loads(row["index_json"])
        if not isinstance(index, dict):
            raise LookupError("Project analysis state is malformed.")
        return index

    def list_project_analyses_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT index_json FROM project_analyses WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
        values = [json.loads(row["index_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def store_project_synthesis_attempt(self, attempt: dict[str, Any]) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_synthesis_attempts (
                    attempt_id, request_id, job_id, conversation_id, folder_access_id,
                    root_fingerprint, analysis_id, provider, model, status,
                    request_hash, response_hash, evidence_hash, patch_id, attempt_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    provider = excluded.provider, model = excluded.model, status = excluded.status,
                    response_hash = excluded.response_hash, patch_id = excluded.patch_id,
                    attempt_json = excluded.attempt_json, updated_at = excluded.updated_at
                """,
                (
                    attempt["attempt_id"], attempt["request_id"], attempt["job_id"],
                    attempt["conversation_id"], attempt["folder_access_id"],
                    attempt["root_fingerprint"], attempt["analysis_id"],
                    str(attempt.get("provider") or "unknown"), str(attempt.get("model") or "unknown"),
                    attempt["status"], attempt["request_hash"], attempt.get("response_hash"),
                    attempt["evidence_hash"], attempt.get("patch_id"),
                    json.dumps(attempt, sort_keys=True), attempt.get("started_at") or now, now,
                ),
            )

    def get_project_synthesis_attempt(self, attempt_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempt_json FROM project_synthesis_attempts WHERE attempt_id = ?", (attempt_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project synthesis attempt not found.")
        value = json.loads(row["attempt_json"])
        if not isinstance(value, dict):
            raise LookupError("Project synthesis attempt state is malformed.")
        return value

    def list_project_synthesis_attempts_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT attempt_json FROM project_synthesis_attempts WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
        values = [json.loads(row["attempt_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def store_project_failure_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_failure_evidence (
                    evidence_id, command_execution_id, job_id, conversation_id,
                    folder_access_id, parent_patch_id, root_fingerprint,
                    project_state_hash, output_hash, status, evidence_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(command_execution_id) DO NOTHING
                """,
                (
                    evidence["evidence_id"], evidence["command_execution_id"],
                    evidence["project_job_id"], evidence["conversation_id"],
                    evidence["folder_access_id"], evidence["parent_patch_id"],
                    evidence["root_fingerprint_after"], evidence["project_state_hash"],
                    evidence["output_hash"], evidence["status"],
                    json.dumps(evidence, sort_keys=True), evidence["created_at"], now,
                ),
            )
            row = connection.execute(
                "SELECT evidence_json FROM project_failure_evidence WHERE command_execution_id = ?",
                (evidence["command_execution_id"],),
            ).fetchone()
        value = json.loads(row["evidence_json"]) if row else None
        if not isinstance(value, dict):
            raise LookupError("Project failure evidence could not be persisted.")
        return value

    def get_project_failure_evidence(self, evidence_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT evidence_json FROM project_failure_evidence WHERE evidence_id = ?", (evidence_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project failure evidence not found.")
        value = json.loads(row["evidence_json"])
        if not isinstance(value, dict):
            raise LookupError("Project failure evidence state is malformed.")
        return value

    def list_project_failure_evidence_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT evidence_json FROM project_failure_evidence WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
        values = [json.loads(row["evidence_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def update_project_failure_evidence(self, evidence: dict[str, Any]) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_failure_evidence SET status = ?, evidence_json = ?, updated_at = ?
                WHERE evidence_id = ? AND job_id = ?
                """,
                (evidence["status"], json.dumps(evidence, sort_keys=True), now,
                 evidence["evidence_id"], evidence["project_job_id"]),
            )
        if cursor.rowcount != 1:
            raise LookupError("Project failure evidence update failed.")

    def store_project_diagnosis(self, diagnosis: dict[str, Any]) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_diagnoses (
                    diagnosis_id, failure_evidence_id, job_id, conversation_id,
                    analysis_id, provider, model, strategy, status, request_hash,
                    response_hash, diagnosis_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(diagnosis_id) DO UPDATE SET
                    provider = excluded.provider, model = excluded.model,
                    strategy = excluded.strategy, status = excluded.status,
                    response_hash = excluded.response_hash,
                    diagnosis_json = excluded.diagnosis_json, updated_at = excluded.updated_at
                """,
                (
                    diagnosis["diagnosis_id"], diagnosis["failure_evidence_id"],
                    diagnosis["job_id"], diagnosis["conversation_id"],
                    diagnosis["analysis_id"], str(diagnosis.get("provider") or "not_invoked"),
                    str(diagnosis.get("model") or "none"), diagnosis.get("strategy"),
                    diagnosis["status"], diagnosis.get("request_hash"),
                    diagnosis.get("response_hash"), json.dumps(diagnosis, sort_keys=True),
                    diagnosis.get("started_at") or now, now,
                ),
            )

    def get_project_diagnosis(self, diagnosis_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT diagnosis_json FROM project_diagnoses WHERE diagnosis_id = ?", (diagnosis_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project diagnosis not found.")
        value = json.loads(row["diagnosis_json"])
        if not isinstance(value, dict):
            raise LookupError("Project diagnosis state is malformed.")
        return value

    def list_project_diagnoses_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT diagnosis_json FROM project_diagnoses WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
        values = [json.loads(row["diagnosis_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def store_project_repair_cycle(self, cycle: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_repair_cycles (
                    repair_cycle_id, repair_chain_id, cycle_number, job_id,
                    conversation_id, parent_patch_id, repair_patch_id,
                    command_execution_id, failure_evidence_id, diagnosis_id,
                    synthesis_attempt_id, root_fingerprint, analysis_id, status,
                    cycle_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(command_execution_id) DO NOTHING
                """,
                (
                    cycle["repair_cycle_id"], cycle["repair_chain_id"], cycle["cycle_number"],
                    cycle["job_id"], cycle["conversation_id"], cycle["parent_patch_id"],
                    cycle.get("repair_patch_id"), cycle["command_execution_id"],
                    cycle["failure_evidence_id"], cycle.get("diagnosis_id"),
                    cycle.get("synthesis_attempt_id"), cycle["root_fingerprint"],
                    cycle.get("analysis_id"), cycle["status"], json.dumps(cycle, sort_keys=True),
                    cycle["created_at"], now,
                ),
            )
            row = connection.execute(
                "SELECT cycle_json FROM project_repair_cycles WHERE command_execution_id = ?",
                (cycle["command_execution_id"],),
            ).fetchone()
        value = json.loads(row["cycle_json"]) if row else None
        if not isinstance(value, dict):
            raise LookupError("Project repair cycle could not be persisted.")
        return value

    def update_project_repair_cycle(self, cycle: dict[str, Any]) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_repair_cycles SET repair_patch_id = ?, diagnosis_id = ?,
                    synthesis_attempt_id = ?, analysis_id = ?, status = ?, cycle_json = ?, updated_at = ?
                WHERE repair_cycle_id = ? AND repair_chain_id = ?
                """,
                (
                    cycle.get("repair_patch_id"), cycle.get("diagnosis_id"),
                    cycle.get("synthesis_attempt_id"), cycle.get("analysis_id"),
                    cycle["status"], json.dumps(cycle, sort_keys=True), now,
                    cycle["repair_cycle_id"], cycle["repair_chain_id"],
                ),
            )
        if cursor.rowcount != 1:
            raise LookupError("Project repair cycle update failed.")

    def get_project_repair_cycle(self, repair_cycle_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cycle_json FROM project_repair_cycles WHERE repair_cycle_id = ?", (repair_cycle_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project repair cycle not found.")
        value = json.loads(row["cycle_json"])
        if not isinstance(value, dict):
            raise LookupError("Project repair cycle state is malformed.")
        return value

    def list_project_repair_cycles_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT cycle_json FROM project_repair_cycles WHERE job_id = ? ORDER BY cycle_number ASC",
                (job_id,),
            ).fetchall()
        values = [json.loads(row["cycle_json"]) for row in rows]
        return [value for value in values if isinstance(value, dict)]

    def get_project_patch(self, patch_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT proposal_json, snapshot_json FROM project_patches WHERE patch_id = ?",
                (patch_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Project patch not found.")
        proposal = json.loads(row["proposal_json"])
        snapshot = json.loads(row["snapshot_json"])
        if not isinstance(proposal, dict) or not isinstance(snapshot, list):
            raise LookupError("Project patch state is malformed.")
        return proposal, snapshot

    def update_project_patch(self, proposal: dict[str, Any], snapshot: list[dict[str, Any]] | None = None) -> None:
        with self._connect() as connection:
            current = connection.execute(
                "SELECT snapshot_json FROM project_patches WHERE patch_id = ?",
                (proposal["patch_id"],),
            ).fetchone()
            if current is None:
                raise LookupError("Project patch not found.")
            snapshot_json = json.dumps(snapshot, sort_keys=True) if snapshot is not None else current["snapshot_json"]
            cursor = connection.execute(
                """
                UPDATE project_patches
                SET status = ?, proposal_json = ?, snapshot_json = ?, updated_at = ?
                WHERE patch_id = ?
                """,
                (
                    proposal["status"], json.dumps(proposal, sort_keys=True), snapshot_json,
                    datetime.now().astimezone().isoformat(), proposal["patch_id"],
                ),
            )
        if cursor.rowcount != 1:
            raise LookupError("Project patch update failed.")

    def transition_project_patch(
        self,
        proposal: dict[str, Any],
        *,
        expected_status: str,
        snapshot: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Atomically claim a project patch lifecycle transition."""
        with self._connect() as connection:
            current = connection.execute(
                "SELECT snapshot_json FROM project_patches WHERE patch_id = ?",
                (proposal["patch_id"],),
            ).fetchone()
            if current is None:
                return False
            snapshot_json = json.dumps(snapshot, sort_keys=True) if snapshot is not None else current["snapshot_json"]
            cursor = connection.execute(
                """
                UPDATE project_patches
                SET status = ?, proposal_json = ?, snapshot_json = ?, updated_at = ?
                WHERE patch_id = ? AND status = ?
                """,
                (
                    proposal["status"], json.dumps(proposal, sort_keys=True), snapshot_json,
                    datetime.now().astimezone().isoformat(), proposal["patch_id"], expected_status,
                ),
            )
        return cursor.rowcount == 1

    def latest_applied_project_patch(self, conversation_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT proposal_json, snapshot_json FROM project_patches
                WHERE conversation_id = ? AND status = 'applied'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise LookupError("No applied Astra patch is available to roll back.")
        return json.loads(row["proposal_json"]), json.loads(row["snapshot_json"])

    def store_project_audit_event(self, event: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_audit_events (
                    event_id, created_at, conversation_id, folder_access_id,
                    patch_id, job_id, command_plan_id, operation, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"], event["created_at"], event["conversation_id"],
                    event["folder_access_id"], event.get("patch_id"), event.get("job_id"),
                    event.get("command_plan_id"), event["operation"], event["status"],
                    json.dumps(event.get("metadata") or {}, sort_keys=True),
                ),
            )

    def list_project_audit_events(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM project_audit_events
                WHERE conversation_id = ? ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [
            {**dict(row), "metadata": json.loads(row["metadata_json"])}
            for row in rows
        ]

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
            rag_sources=_json_list(row["rag_sources_json"]),
            source_count=int(row["source_count"]),
            source_paths=[
                str(item)
                for item in _json_list(row["source_paths_json"])
                if isinstance(item, str)
            ],
            grounding_status=(
                str(row["grounding_status"])
                if row["grounding_status"] in {"grounded", "weak", "none"}
                else "none"
            ),
            corpus_retrieval_used=bool(row["corpus_retrieval_used"]),
            corpus_retrieval_skip_reason=(
                str(row["corpus_retrieval_skip_reason"])
                if row["corpus_retrieval_skip_reason"]
                else None
            ),
            corpus_context_count=int(row["corpus_context_count"]),
            corpus_sources=_json_list(row["corpus_sources_json"]),
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
            action=(json.loads(row["action_json"]) if row["action_json"] else None),
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


def _json_list(raw: object) -> list:
    if not isinstance(raw, str) or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
