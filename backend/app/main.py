from __future__ import annotations

import re

import hashlib
import json
import os
import queue
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.analyzer import add_validated_fixes, analyze_python_code
from backend.app.analyzer.patch_apply import (
    PatchApplyConflictError,
    apply_patch_proposal,
)
from backend.app.analyzer.patch_preview import preview_patch_proposal
from backend.app.analyzer.patch_verification import run_pytest_verification
from backend.app.analyzer.rules.metadata import get_rule_metadata
from backend.app.assignments import (
    AssignmentBrief,
    AssignmentCodeBlueprintSet,
    AssignmentEvidenceChecklist,
    AssignmentPlan,
    AssignmentProjectManifest,
    AssignmentTemplatePlan,
    build_assignment_manifest,
    build_assignment_plan,
    build_evidence_checklist,
    build_final_readiness_report,
    check_marking_readiness,
    extract_assignment_brief,
    export_report_package,
    generate_analysis_plan,
    generate_code_blueprints,
    generate_dashboard_spec,
    generate_assignment_runbook,
    generate_assignment_template_plan,
    generate_report_draft,
    generate_report_skeleton,
    generate_task_breakdown,
    map_dataset_columns,
    parse_assignment_document,
    plan_assignment_workspace,
    run_assignment_copilot,
    update_evidence_status,
    write_assignment_manifest,
    write_code_blueprints,
    write_assignment_template_plan,
)
from backend.app.assignments.grounded_generation import (
    write_grounded_workspace,
)
from backend.app.assignments.schemas import (
    CorpusGroundingSummary,
    GenerationMode,
    GroundedFileBlueprint,
)
from backend.app.assignments.verification import (
    AssignmentVerificationError,
    load_verification_snapshot,
    record_manual_evidence_review,
    verify_assignment_workspace,
)
from backend.app.assignments.report_assembly import (
    ReportAssemblyError,
    assemble_grounded_report,
    create_grounded_report,
    export_grounded_report,
    list_grounded_reports,
    list_report_exports,
    load_grounded_report,
    report_export_readiness,
    resolve_report_export,
    update_grounded_report,
)
from backend.app.benchmark.trace_compactor import compact_orchestrator_trace
from backend.app.commands import (
    CommandExecutionError,
    analyze_command,
    approve_assignment_command,
    cancel_assignment_command,
    execute_assignment_command,
    get_assignment_command,
    get_assignment_execution_summary,
    plan_assignment_command,
    suggest_assignment_actions,
    suggest_command,
    validate_assignment_command_execution,
)
from backend.app.core.path_utils import resolve_user_path
from backend.app.chat_runtime.service import CanonicalChatRuntimeService
from backend.app.chat_workflow import run_chat_workflow
from backend.app.chat_actions import DetectedChatAction, detect_chat_action
from backend.app.database.repository import AnalysisRepository
from backend.app.database.migrations import LATEST_SCHEMA_VERSION, current_schema_version
from backend.app.debugging import analyze_error_output
from backend.app.datasets import profile_csv_dataset
from backend.app.hardware_ai_optimizer import (
    HardwareOptimizerResponse,
    probe_hardware,
    recommend_training_settings,
)
from backend.app.jobs import JobQueue
from backend.app.intelligence import (
    build_intelligence_dashboard,
    decision_traces_from_chat_runs,
    intelligence_components,
    model_use_policy,
    worker_roles,
)
from backend.app.local_runtime import (
    ExecutionProfile,
    PlanValidationResult,
    RuntimeContext,
    RuntimeResearchManifest,
    build_execution_profile,
    build_runtime_context,
    get_runtime_research_manifest,
    validate_task_plan,
)
from backend.app.rag.corpus_inventory import scan_corpus
from backend.app.rag.corpus_index_store import (
    DEFAULT_INDEX_ROOT as DEFAULT_CORPUS_INDEX_ROOT,
    build_corpus_index,
    corpus_index_files,
    corpus_index_status,
)
from backend.app.rag.corpus_index_preview import build_corpus_index_preview
from backend.app.rag.corpus_text_extractor import extract_indexable_corpus
from backend.app.rag.corpus_chunker import build_corpus_chunk_preview
from backend.app.rag.corpus_search import search_corpus_vectors
from backend.app.rag.corpus_vector_store import (
    DEFAULT_VECTOR_ROOT as DEFAULT_CORPUS_VECTOR_ROOT,
    CorpusVectorStoreError,
    build_corpus_vectors,
    corpus_vector_files,
    corpus_vector_status,
)
from backend.app.rag.deterministic_embeddings import (
    DeterministicEmbeddingProvider,
)
from backend.app.rag.context_service import (
    compact_context,
    rag_build_project_index,
    rag_indexed_files,
    rag_project_index_status,
    rag_search,
    rag_status,
)
from backend.app.rag.evaluation import (
    evaluate_project_rag,
    rag_evaluation_status,
)
from backend.app.orchestrator.approvals import approve_pending_patch
from backend.app.orchestrator.policy import PolicyError
from backend.app.schemas.api import (
    AnalyzeFileRequest,
    AnalyzeProjectRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    ChatConversationDeleteResponse,
    ChatConversationDetail,
    ChatConversationsResponse,
    ChatRequestRecord,
    ChatRunRequest,
    ChatRunResponse,
    ChatRunsResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    HistoryResponse,
    JobAcceptedResponse,
    JobResponse,
    JobsResponse,
    JobStatus,
    MetricsResponse,
    ExecutionProfileRequest,
    OrchestrateRequest,
    PatchApplyRequest,
    PatchApplyResponse,
    PatchPreviewRequest,
    PatchPreviewResponse,
    PatchProposalResponse,
    RulesResponse,
    RuntimePlanValidationRequest,
    ToolsResponse,
)
from backend.app.specialists.routes import router as specialists_router
from backend.app.project_validation.routes import router as project_validation_router
from backend.app.slm import (
    SLMChatRequest,
    SLMIntentRequest,
    chat_with_slm,
    get_selected_slm_profile,
    get_slm_gateway_status,
    infer_intent_with_slm,
    list_slm_profiles,
    select_slm_profile,
)
from backend.app.tools import get_tool_metadata
from backend.app.training_data import (
    export_examples,
    get_dataset_status,
    list_examples,
    log_chat_run_example,
    log_manual_example,
    update_example_label,
)
from backend.app.training_data.schemas import (
    TrainingExampleCreateRequest,
    TrainingExampleLabelRequest,
    TrainingExportRequest,
)
from backend.app.folders import (
    FolderScanError,
    ProjectPatchError,
    ProjectSafetyError,
    apply_project_patch,
    build_inventory,
    completed_folder_access,
    create_patch_proposal,
    create_folder_content_unavailable_chat_run,
    create_folder_chat_run,
    create_project_chat_run,
    detect_explicit_patch_request,
    detect_project_intent,
    detect_folder_request,
    diff_inventories,
    has_completed_folder_action,
    is_folder_content_request,
    project_root_fingerprint,
    public_patch_proposal,
    rollback_project_patch,
    validate_folder_root,
    validate_root_identity,
    verify_patch_approval,
)
from backend.app.folders.audit import audit_event
from backend.app.project_analysis import (
    ProjectAnalysisError,
    ProjectManifestError,
    IncompleteProjectManifestError,
    build_project_state_manifest,
    analysis_audit_metadata,
    build_analysis_plan,
    build_project_index,
    public_index,
)
from backend.app.project_analysis.model_synthesis import (
    ModelSynthesisError,
    SynthesisGateway,
    SynthesisProposalStore,
    build_synthesis_gateway_from_environment,
)
from backend.app.project_analysis.diagnosis import (
    DiagnosisError,
    MAX_DIAGNOSIS_MODEL_CALLS,
    ProjectFailureEvidence,
    build_failure_evidence,
    deterministic_diagnosis,
    diagnose_project_failure,
    project_state_hash,
)
from backend.app.project_jobs import (
    MAX_REPAIR_CYCLES,
    MAX_REPAIR_FAILURES,
    ProjectJobError,
    answer_clarification,
    build_completion_summary,
    build_job_action,
    build_job_chat_run,
    create_project_job,
    detect_project_job_followup,
    detect_project_delivery_task,
    detect_project_task,
    detect_repair_request,
    interpret_validation_result,
    prepare_job_patch_bundle,
    public_project_job,
)
from backend.app.project_delivery import (
    DeliveryStatus,
    ProjectDeliveryError,
    ProjectVerifierError,
    VerificationMode,
    VerificationState,
    VerifierOutcome,
    adapt_legacy_delivery_job,
    activate_next_work_unit,
    approve_plan as approve_delivery_plan,
    build_delivery_action,
    build_delivery_chat_run,
    cancel_delivery,
    create_delivery_job,
    generate_handoff,
    link_patch_preview,
    public_delivery_job,
    record_patch_applied as record_delivery_patch_applied,
    record_rollback as record_delivery_rollback,
    revise_scope as revise_delivery_scope,
    record_verification as record_delivery_verification,
    submit_clarification as submit_delivery_clarification,
    record_scope_change as record_delivery_scope_change,
    run_deterministic_verifier,
)
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlErrorCode,
    ProjectControlPlane,
)
from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_api import (
    CanonicalProjectResponse,
    build_canonical_project_response,
    create_project_router,
)
from backend.app.project_control.adapters import ProjectDeliveryControlAdapter
from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_retrieval import (
    ProjectRetrievalService,
    build_retrieval_providers,
    create_project_retrieval_router,
    rag_provider_capabilities,
    retrieval_configuration_from_environment,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.local_ai.routes import create_local_ai_router
from backend.app.runtime import build_runtime_manager
from backend.app.runtime.routes import create_runtime_router
from backend.app.local_ai.contracts import Capability, CapabilityStatus
from backend.app.project_coordinator import (
    CoordinatorIntentError,
    ProjectCoordinatorService,
)
from backend.app.project_projection import ProjectProjectionService
from backend.app.project_workers.cancellation import CancellationDispatcher
from backend.app.project_workers.reconciliation import TerminalResultReconciler
from backend.app.project_workers import (
    DockerIsolationBackend,
    ExecutionInputArtifact,
    FileMutationKind,
    FileMutationEngine,
    FileMutationError,
    FileOperationKind,
    FileOperationSpec,
    ProjectWorkerQueue,
    ProjectWorkerService,
    build_execution_spec,
    build_file_mutation_spec,
    calculate_expected_manifest_hash,
    default_isolation_profile,
)
from backend.app.client_engagement import (
    EngagementError,
    EngagementService,
    EngagementState,
    ScopeRevision as EngagementScopeRevision,
    build_engagement_chat_run,
    detect_engagement_request,
    public_engagement,
    stage9_task_from_scope,
)
from backend.app.workspace import inspect_workspace


APP_VERSION = "0.5.0"
APP_PHASE = "release-4-feedback"
DEFAULT_DATABASE_PATH = Path("data/app/ai_system.db")
DEFAULT_WORKSPACE_ROOT = Path.cwd()


class SLMSelectRequest(BaseModel):
    profile_id: str


class RAGSearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=5, ge=0, le=20)
    source_filter: str | None = None


class RAGEvaluationRequest(BaseModel):
    selected_cases: list[str] = Field(default_factory=list)


class CorpusIndexBuildRequest(BaseModel):
    full_rebuild: bool = False
    max_chars: int = Field(default=4000, ge=100, le=20000)
    overlap_chars: int = Field(default=400, ge=0, le=5000)


class CorpusEmbeddingBuildRequest(BaseModel):
    full_rebuild: bool = False


class CorpusSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    minimum_score: float | None = Field(default=0.0, ge=-1.0, le=1.0)
    source_path: str | None = None


class SLMChatWithContextRequest(BaseModel):
    message: str = ""
    limit: int = Field(default=4, ge=0, le=10)
    source_filter: str | None = None


ASSIGNMENT_DOCUMENT_EXTENSIONS = {".txt", ".md", ".docx"}
MAX_ASSIGNMENT_UPLOAD_BYTES = 10 * 1024 * 1024


class AssignmentParseRequest(BaseModel):
    path: str = Field(..., min_length=1)


class AssignmentExtractRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    title: str | None = None


class AssignmentPlanRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None


class AssignmentEvidenceBuildRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None


class AssignmentEvidenceUpdateStatusRequest(BaseModel):
    checklist: dict
    evidence_id: str
    status: str
    notes: str | None = None


class AssignmentReportDraftRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    plan: dict | None = None
    evidence: dict | None = None
    project_metadata: dict | None = None


class AssignmentReportSkeletonRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    dataset_path: str | None = None
    evidence: dict | None = None


class AssignmentTaskBreakdownRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    evidence: dict | None = None


class AssignmentMarkingCheckRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    evidence: dict | None = None


class AssignmentCopilotRunRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    selected_assignment: str | int | None = "all"
    workspace_path: str | None = None
    dataset_path: str | None = None
    project_metadata: dict | None = None
    use_corpus: bool = True
    generation_mode: GenerationMode = "mixed"


class AssignmentWorkspaceGenerateRequest(BaseModel):
    assignment_number: int = Field(..., ge=1, le=3)
    workspace_path: str = Field(..., min_length=1)
    generation_mode: GenerationMode = "mixed"
    overwrite: bool = False
    copilot_result: dict


class ChatAssignmentAnalyzeRequest(AssignmentCopilotRunRequest):
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    user_message: str | None = Field(default=None, min_length=1, max_length=1000)


class ChatAssignmentActionRequest(BaseModel):
    chat_run_id: str = Field(..., min_length=1, max_length=128)


class ChatFolderRequest(BaseModel):
    path: str = Field(..., min_length=1)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    user_message: str | None = Field(default=None, min_length=1, max_length=1000)


class ChatFolderActionRequest(BaseModel):
    chat_run_id: str = Field(..., min_length=1, max_length=128)


class ProjectPatchChangeRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    operation: str = Field(default="modify", min_length=1, max_length=20)
    content: str | None = Field(default=None, max_length=250_000)
    explanation: str | None = Field(default=None, max_length=1000)


class ProjectPatchProposalRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    user_request: str = Field(..., min_length=1, max_length=2000)
    changes: list[ProjectPatchChangeRequest] = Field(..., min_length=1, max_length=10)
    files_inspected: list[str] = Field(default_factory=list, max_length=80)
    validation_plan: list[str] = Field(default_factory=list, max_length=20)


class ProjectPatchApprovalRequest(BaseModel):
    chat_run_id: str = Field(..., min_length=1, max_length=128)
    confirmation: str = Field(..., min_length=1, max_length=200)


class ProjectPatchApplyRequest(BaseModel):
    chat_run_id: str = Field(..., min_length=1, max_length=128)


class ProjectRollbackRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    user_message: str = Field(default="Undo the last Astra change.", min_length=1, max_length=1000)


class ProjectCommandProposalRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=50)
    target: str | None = Field(default=None, max_length=500)
    purpose: str = Field(default="Validate the approved project.", min_length=1, max_length=1000)
    expected_result: str = Field(default="A bounded validation result.", min_length=1, max_length=1000)
    timeout_seconds: int = Field(default=120, ge=1, le=120)


class ProjectJobActionRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=160)
    actor_id: str | None = Field(default=None, min_length=1, max_length=256)
    repository_root_fingerprint: str | None = Field(default=None, min_length=1, max_length=256)
    project_run_id: str | None = Field(default=None, min_length=1, max_length=160)
    plan_revision_id: str | None = Field(default=None, min_length=1, max_length=160)
    scope_revision_id: str | None = Field(default=None, min_length=1, max_length=160)
    manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=200)
    artifact_type: str | None = Field(default=None, min_length=1, max_length=80)
    artifact_hash: str | None = Field(default=None, min_length=64, max_length=64)
    artifact_binding_hash: str | None = Field(default=None, min_length=64, max_length=64)
    coordinator_intent_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_state_version: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class HistoricalProjectImportRequest(BaseModel):
    model_config = {"extra": "forbid"}
    conversation_id: str = Field(..., min_length=1, max_length=128)
    workspace_id: str = Field(..., min_length=1, max_length=160)
    actor_id: str = Field(..., min_length=1, max_length=256)
    repository_root_fingerprint: str = Field(..., min_length=1, max_length=256)
    historical_source_id: str = Field(..., min_length=1, max_length=160)
    idempotency_key: str = Field(..., min_length=1, max_length=200)


class ProjectJobClarificationRequest(ProjectJobActionRequest):
    answer: str = Field(..., min_length=1, max_length=2000)


class ProjectDeliveryStartRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    user_request: str = Field(..., min_length=1, max_length=3000)


class ProjectDeliveryHashRequest(ProjectJobActionRequest):
    immutable_hash: str = Field(..., min_length=64, max_length=64)


class ProjectDeliveryClarificationRequest(ProjectJobActionRequest):
    answer: str = Field(..., min_length=1, max_length=2000)


class ProjectDeliveryVerificationRequest(ProjectJobActionRequest):
    criterion_id: str = Field(..., min_length=1, max_length=80)


class EngagementCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    client_request: str = Field(..., min_length=1, max_length=6000)
    user_id: str = Field(default="local-user", min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    organization: str | None = Field(default=None, max_length=256)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class EngagementVersionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    conversation_id: str = Field(..., min_length=1, max_length=128)
    expected_state_version: int = Field(..., ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class EngagementAnswerRequest(EngagementVersionRequest):
    answers: dict[str, str] = Field(default_factory=dict)
    use_reasonable_assumptions: bool = False
    answered_by: str = Field(default="local-user", min_length=1, max_length=256)


class EngagementApprovalRequest(EngagementVersionRequest):
    revision_id: str = Field(..., min_length=1, max_length=128)
    scope_hash: str = Field(..., min_length=64, max_length=64)
    approving_user: str = Field(default="local-user", min_length=1, max_length=256)


class EngagementRejectionRequest(EngagementVersionRequest):
    revision_id: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(..., min_length=1, max_length=2000)
    rejecting_user: str = Field(default="local-user", min_length=1, max_length=256)


class EngagementScopeChangeApiRequest(EngagementVersionRequest):
    requested_change: str = Field(..., min_length=1, max_length=4000)
    requested_by: str = Field(default="local-user", min_length=1, max_length=256)


class DatasetProfileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    sample_rows: int = Field(default=25, ge=1, le=100)
    row_count_override: int | None = Field(default=None, ge=0)


class AssignmentWorkspacePlanRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    assignment_number: int = Field(..., ge=1, le=3)
    workspace_path: str | None = None
    dataset_path: str | None = None
    write_files: bool = False
    overwrite: bool = False


class AssignmentRunbookGenerateRequest(BaseModel):
    assignment_number: int = Field(..., ge=1, le=3)
    workspace_path: str | None = None


class AssignmentReportExportRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    assignment_number: int = Field(default=1, ge=1, le=3)
    workspace_path: str | None = None
    report_folder: str = "report_package"
    overwrite: bool = False


class AssignmentCodeBlueprintRequest(BaseModel):
    assignment_number: int = Field(..., ge=1, le=3)
    dataset_path: str | None = None


class AssignmentCodeWriteRequest(BaseModel):
    assignment_number: int | None = Field(default=None, ge=1, le=3)
    assignment_numbers: list[int] = Field(default_factory=list)
    workspace_path: str | None = None
    dataset_path: str | None = None
    blueprints: list[dict] | dict | None = None
    overwrite: bool = False


class AssignmentDatasetMapRequest(BaseModel):
    dataset_path: str | None = None
    dataset_profile: dict | None = None


class AssignmentManifestBuildRequest(BaseModel):
    copilot_result: dict
    assignment_number: int = Field(..., ge=1, le=3)
    dataset_path: str | None = None
    document_path: str | None = None


class AssignmentManifestWriteRequest(BaseModel):
    copilot_result: dict | None = None
    manifest: dict | None = None
    assignment_number: int = Field(..., ge=1, le=3)
    workspace_path: str | None = None
    dataset_path: str | None = None
    document_path: str | None = None
    overwrite: bool = False


class AssignmentAnalysisPlanRequest(BaseModel):
    assignment_number: int = Field(..., ge=1, le=3)
    dataset_path: str | None = None


class AssignmentDashboardSpecRequest(BaseModel):
    assignment_number: int = Field(..., ge=1, le=3)
    dataset_path: str | None = None


class AssignmentFinalReadinessRequest(BaseModel):
    path: str | None = None
    text: str | None = None
    brief: dict | None = None
    assignment_number: int = Field(..., ge=1, le=3)
    workspace_path: str | None = None
    dataset_path: str | None = None


class WorkspaceInspectRequest(BaseModel):
    path: str | None = None
    max_files: int = Field(default=250, ge=1, le=1000)


class AssignmentTemplatePlanRequest(BaseModel):
    assignment_number: int = Field(..., ge=1, le=3)


class AssignmentTemplateWriteRequest(BaseModel):
    assignment_number: int | None = Field(default=None, ge=1, le=3)
    plan: dict | None = None
    workspace_path: str | None = None
    overwrite: bool = False


class CommandSuggestRequest(BaseModel):
    action: str | None = None
    command: str | None = None
    target: str | None = None
    working_directory: str | None = None


class AssignmentCommandPlanRequest(BaseModel):
    assignment_id: str = Field(..., min_length=1, max_length=128)
    assignment_task: str = Field(..., min_length=1, max_length=1000)
    expected_result: str = Field(..., min_length=1, max_length=1000)
    action: str = Field(..., min_length=1)
    workspace_path: str = Field(..., min_length=1)
    target: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class AssignmentCommandApprovalRequest(BaseModel):
    assignment_id: str = Field(..., min_length=1, max_length=128)
    workspace_path: str = Field(..., min_length=1)
    confirmation: str = Field(..., min_length=1)
    chat_run_id: str | None = Field(default=None, min_length=1, max_length=128)


class AssignmentCommandAssociationRequest(BaseModel):
    assignment_id: str = Field(..., min_length=1, max_length=128)
    workspace_path: str = Field(..., min_length=1)
    chat_run_id: str | None = Field(default=None, min_length=1, max_length=128)


class AssignmentCommandExecuteRequest(BaseModel):
    assignment_id: str = Field(..., min_length=1, max_length=128)
    workspace_path: str = Field(..., min_length=1)
    approval_token: str = Field(..., min_length=1)
    chat_run_id: str | None = Field(default=None, min_length=1, max_length=128)


class AssignmentVerifyRequest(BaseModel):
    workspace_path: str = Field(..., min_length=1)
    assignment_output: dict


class AssignmentEvidenceReviewRequest(BaseModel):
    workspace_path: str = Field(..., min_length=1)
    requirement_id: str = Field(..., min_length=1, max_length=256)
    evidence_reference: str = Field(..., min_length=1, max_length=1000)
    decision: str = Field(..., min_length=1, max_length=32)
    note: str = Field(default="", max_length=4000)


class AssignmentReportCreateRequest(BaseModel):
    workspace_path: str = Field(..., min_length=1)
    title: str | None = Field(default=None, max_length=300)


class AssignmentReportUpdateRequest(BaseModel):
    workspace_path: str = Field(..., min_length=1)
    changes: dict


class AssignmentReportWorkspaceRequest(BaseModel):
    workspace_path: str = Field(..., min_length=1)


class AssignmentReportExportRequestV2(BaseModel):
    workspace_path: str = Field(..., min_length=1)
    format: str = Field(..., min_length=1, max_length=20)
    selected_files: list[str] = Field(default_factory=list)


class DebugAnalyzeErrorRequest(BaseModel):
    output: str = ""
    project_path: str | None = None


def create_app(
    database_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
    *,
    project_synthesis_gateway: SynthesisGateway | None = None,
    project_diagnosis_gateway: SynthesisGateway | None = None,
) -> FastAPI:
    configured_path = database_path or os.getenv(
        "AI_SYSTEM_DB_PATH", str(DEFAULT_DATABASE_PATH)
    )
    configured_workspace_root = Path(
        workspace_root
        or os.getenv("AI_SYSTEM_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))
    ).expanduser().resolve()
    assignment_command_store = (
        configured_workspace_root / "data" / "assignment_command_runs"
    )
    assignment_verification_store = (
        configured_workspace_root / "data" / "assignment_verification"
    )
    assignment_report_store = configured_workspace_root / "data" / "assignment_reports"

    repository = AnalysisRepository(configured_path)
    project_artifact_store = ProjectArtifactStore(configured_path)
    project_control = ProjectControlPlane(
        configured_path, artifact_store=project_artifact_store
    )
    canonical_project_service = CanonicalProjectService(
        project_control, project_artifact_store
    )
    local_ai_service = LocalAIService(configured_path)
    rag_configuration = retrieval_configuration_from_environment()
    rag_embedding, rag_reranker = build_retrieval_providers(
        rag_configuration, local_ai=local_ai_service
    )
    project_retrieval_service = ProjectRetrievalService(
        configured_path,
        project_control,
        project_artifact_store,
        embedding_provider=rag_embedding,
        reranker=rag_reranker,
    )
    def _rag_provider_capabilities() -> tuple[Capability, ...]:
        return rag_provider_capabilities(rag_embedding, rag_reranker)
    local_ai_service.set_additional_capability_probe(_rag_provider_capabilities)
    chat_runtime_service = CanonicalChatRuntimeService(
        local_ai_service=local_ai_service,
        project_control=project_control,
        project_retrieval_service=project_retrieval_service,
    )
    delivery_control = ProjectDeliveryControlAdapter(
        project_control, project_artifact_store
    )
    project_worker_queue = ProjectWorkerQueue(configured_path)
    project_mutation_engine = FileMutationEngine(
        configured_path,
        configured_workspace_root / "data" / "project_mutation_journals",
    )
    project_coordinator = ProjectCoordinatorService(configured_path, project_control)
    synthesis_proposal_store = SynthesisProposalStore(configured_path)
    project_projection = ProjectProjectionService(configured_path, project_control)
    phase3c_recovery_enabled = (
        os.getenv("ASTRA_PROJECT_RECONCILIATION_ENABLED", "1").strip() != "0"
    )
    terminal_reconciler = TerminalResultReconciler(
        project_control,
        project_worker_queue,
        project_artifact_store,
        projector=project_projection if phase3c_recovery_enabled else None,
        coordinator=project_coordinator if phase3c_recovery_enabled else None,
    )
    project_worker_service = ProjectWorkerService(
        project_control,
        project_worker_queue,
        terminal_reconciler=terminal_reconciler,
    )
    cancellation_dispatcher = (
        CancellationDispatcher(
            project_control, project_worker_service, project_artifact_store
        )
        if phase3c_recovery_enabled else None
    )
    job_queue = JobQueue(configured_path)
    synthesis_gateway = project_synthesis_gateway or build_synthesis_gateway_from_environment(
        configured_path
    )
    diagnosis_gateway = project_diagnosis_gateway or synthesis_gateway
    engagement_service = EngagementService(repository, model_gateway=synthesis_gateway)
    synthesis_lock = threading.Lock()
    repair_lock = threading.Lock()
    delivery_lock = threading.Lock()
    engagement_lock = threading.Lock()

    runtime_manager = build_runtime_manager(
        database_path=configured_path,
        repository=repository,
        project_control=project_control,
        project_artifact_store=project_artifact_store,
        project_retrieval_service=project_retrieval_service,
        local_ai_service=local_ai_service,
        project_worker_queue=project_worker_queue,
        project_mutation_engine=project_mutation_engine,
        project_coordinator=project_coordinator,
        synthesis_proposal_store=synthesis_proposal_store,
        job_queue=job_queue,
        rag_embedding=rag_embedding,
        rag_reranker=rag_reranker,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # RuntimeManager is now the sole lifecycle authority: it calls each
        # subsystem's initialize() in the same order this block used to
        # (repository, project_control, project_artifact_store,
        # project_retrieval, project_worker_queue, project_mutation_engine,
        # project_coordinator, synthesis_proposal_store, local_ai, then
        # job_queue last), plus a startup recovery pass covering what
        # project_coordinator.recover_expired_leases() used to do alone.
        runtime_manager.initialize()
        yield
        runtime_manager.shutdown()

    application = FastAPI(
        title="AI Coding Assistant",
        description="Local-first Python code analysis service.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


    application.state.analysis_repository = repository
    application.state.project_control = project_control
    application.state.project_artifact_store = project_artifact_store
    application.state.canonical_project_service = canonical_project_service
    application.state.project_retrieval_service = project_retrieval_service
    application.state.local_ai_service = local_ai_service
    application.state.project_worker_queue = project_worker_queue
    application.state.project_worker_service = project_worker_service
    application.state.project_mutation_engine = project_mutation_engine
    application.state.project_coordinator = project_coordinator
    application.state.synthesis_proposal_store = synthesis_proposal_store
    application.state.project_projection = project_projection
    application.state.cancellation_dispatcher = cancellation_dispatcher
    application.state.job_queue = job_queue
    application.state.workspace_root = configured_workspace_root
    application.state.runtime_manager = runtime_manager
    application.include_router(specialists_router)
    application.include_router(project_validation_router)
    application.include_router(create_local_ai_router(local_ai_service))
    application.include_router(create_runtime_router(runtime_manager))

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="ai-coding-assistant-backend",
            version=APP_VERSION,
            phase=APP_PHASE,
            database=repository.status(),
            timestamp=datetime.now(timezone.utc),
        )


    @application.get("/chat/projects/runtime-capabilities")
    def project_runtime_capabilities() -> dict:
        execution_backend = os.getenv(
            "ASTRA_PROJECT_EXECUTION_BACKEND",
            "docker",
        ).strip().lower()
        profile = default_isolation_profile()
        capability = (
            DockerIsolationBackend(profile).probe()
            if execution_backend == "docker" else None
        )
        workers = project_worker_service.list_active_runtime_instances()
        database_schema_version = current_schema_version(configured_path)
        return {
            "schema_version": "astra.project-workers.runtime-capabilities.v1",
            "execution_backend": execution_backend,
            "worker_process_required": True,
            "worker_available": bool(workers),
            "active_workers": [worker.model_dump(mode="json") for worker in workers],
            "isolation_profile": profile.model_dump(mode="json"),
            "isolation_capability": (
                capability.model_dump(mode="json") if capability is not None else None
            ),
            "supported_attempt_types": [
                "patch_application",
                "rollback",
                "command_execution",
                "verification",
            ],
            "supported_toolchains": ["python", "node"],
            "host_execution_fallback": False,
            "database_schema_version": database_schema_version,
            "database_migration_status": (
                "current"
                if database_schema_version == LATEST_SCHEMA_VERSION
                else "upgrade_required"
            ),
        }
    @application.get(
        "/hardware-ai/report",
        response_model=HardwareOptimizerResponse,
    )
    def hardware_ai_report() -> HardwareOptimizerResponse:
        report = probe_hardware(configured_workspace_root)
        return HardwareOptimizerResponse(
            report=report,
            recommendations=recommend_training_settings(report),
        )

    @application.get("/runtime/context", response_model=RuntimeContext)
    def runtime_context(task: str | None = Query(default=None)) -> RuntimeContext:
        return build_runtime_context(
            task=task,
            workspace_root=configured_workspace_root,
        )

    @application.get(
        "/runtime/research-manifest",
        response_model=RuntimeResearchManifest,
    )
    def runtime_research_manifest() -> RuntimeResearchManifest:
        return get_runtime_research_manifest()

    @application.post(
        "/runtime/validate-plan",
        response_model=PlanValidationResult,
    )
    def runtime_validate_plan(
        request: RuntimePlanValidationRequest,
    ) -> PlanValidationResult:
        context = build_runtime_context(
            task=request.task,
            workspace_root=configured_workspace_root,
        )
        return validate_task_plan(
            task=request.task,
            requested_plan=request.requested_plan,
            runtime_context=context,
        )

    @application.post(
        "/runtime/execution-profile",
        response_model=ExecutionProfile,
    )
    def runtime_execution_profile(
        request: ExecutionProfileRequest,
    ) -> ExecutionProfile:
        context = build_runtime_context(
            task=request.task,
            workspace_root=configured_workspace_root,
        )
        validation = validate_task_plan(
            task=request.task,
            requested_plan=request.requested_plan,
            runtime_context=context,
        )
        if validation.decision == "block":
            raise HTTPException(status_code=409, detail=validation.reason)
        active_plan = (
            validation.recommended_plan
            if validation.decision == "downgrade"
            else validation.requested_plan
        )
        return build_execution_profile(
            task=request.task,
            runtime_context=context,
            active_runtime_plan=active_plan,
        )

    @application.get("/runtime/slm/profiles")
    def runtime_slm_profiles() -> dict:
        return list_slm_profiles()

    @application.get("/runtime/slm/selected")
    def runtime_slm_selected() -> dict:
        return get_selected_slm_profile()

    @application.post("/runtime/slm/select")
    def runtime_slm_select(request: SLMSelectRequest) -> dict:
        result = select_slm_profile(request.profile_id)
        if result.get("selected") is not True:
            raise HTTPException(status_code=400, detail=result.get("reason", "Invalid SLM profile."))
        return result

    @application.get("/runtime/slm/status")
    def runtime_slm_status() -> dict:
        return get_slm_gateway_status()

    @application.post("/slm/chat")
    def slm_chat(request: SLMChatRequest) -> dict:
        return chat_with_slm(request.message, request.context)

    @application.post("/slm/intent")
    def slm_intent(request: SLMIntentRequest) -> dict:
        return infer_intent_with_slm(request.message, request.context)

    @application.get("/rag/corpus/inventory")
    def local_rag_corpus_inventory() -> dict:
        return scan_corpus()

    @application.get("/rag/corpus/index-preview")
    def local_rag_corpus_index_preview() -> dict:
        return build_corpus_index_preview()

    @application.get("/rag/corpus/extraction-preview")
    def local_rag_corpus_extraction_preview(
        limit: int = Query(default=100, ge=0, le=500),
        include_text: bool = Query(default=False),
    ) -> dict:
        return extract_indexable_corpus(
            limit=limit,
            include_text=include_text,
        )

    @application.get("/rag/corpus/chunk-preview")
    def local_rag_corpus_chunk_preview(
        file_limit: int = Query(default=100, ge=0, le=500),
        include_text: bool = Query(default=False),
        max_chars: int = Query(default=4000, ge=100, le=20000),
        overlap_chars: int = Query(default=400, ge=0, le=5000),
    ) -> dict:
        if overlap_chars >= max_chars:
            raise HTTPException(
                status_code=400,
                detail="overlap_chars must be smaller than max_chars",
            )

        return build_corpus_chunk_preview(
            file_limit=file_limit,
            include_text=include_text,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

    @application.post("/rag/corpus/index/build")
    def local_rag_corpus_index_build(
        request: CorpusIndexBuildRequest | None = None,
    ) -> dict:
        build_request = request or CorpusIndexBuildRequest()

        if build_request.overlap_chars >= build_request.max_chars:
            raise HTTPException(
                status_code=400,
                detail="overlap_chars must be smaller than max_chars",
            )

        return build_corpus_index(
            configured_workspace_root / "astra_corpus",
            index_root=configured_workspace_root
            / DEFAULT_CORPUS_INDEX_ROOT,
            full_rebuild=build_request.full_rebuild,
            max_chars=build_request.max_chars,
            overlap_chars=build_request.overlap_chars,
        )

    @application.get("/rag/corpus/index/status")
    def local_rag_corpus_index_status() -> dict:
        return corpus_index_status(
            configured_workspace_root / DEFAULT_CORPUS_INDEX_ROOT,
        )

    @application.get("/rag/corpus/index/files")
    def local_rag_corpus_index_files() -> dict:
        return corpus_index_files(
            configured_workspace_root / DEFAULT_CORPUS_INDEX_ROOT,
        )

    @application.post("/rag/corpus/embeddings/build")
    def local_rag_corpus_embeddings_build(
        request: CorpusEmbeddingBuildRequest | None = None,
    ) -> dict:
        build_request = request or CorpusEmbeddingBuildRequest()
        provider = DeterministicEmbeddingProvider()
        try:
            return build_corpus_vectors(
                provider,
                index_root=(
                    configured_workspace_root / DEFAULT_CORPUS_INDEX_ROOT
                ),
                vector_root=(
                    configured_workspace_root / DEFAULT_CORPUS_VECTOR_ROOT
                ),
                full_rebuild=build_request.full_rebuild,
            )
        except CorpusVectorStoreError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.get("/rag/corpus/embeddings/status")
    def local_rag_corpus_embeddings_status() -> dict:
        return corpus_vector_status(
            configured_workspace_root / DEFAULT_CORPUS_VECTOR_ROOT,
            provider=DeterministicEmbeddingProvider(),
        )

    @application.get("/rag/corpus/embeddings/files")
    def local_rag_corpus_embeddings_files() -> dict:
        return corpus_vector_files(
            configured_workspace_root / DEFAULT_CORPUS_VECTOR_ROOT,
            provider=DeterministicEmbeddingProvider(),
        )

    @application.post("/rag/corpus/search")
    def local_rag_corpus_search(request: CorpusSearchRequest) -> dict:
        try:
            return search_corpus_vectors(
                request.query,
                DeterministicEmbeddingProvider(),
                vector_root=(
                    configured_workspace_root / DEFAULT_CORPUS_VECTOR_ROOT
                ),
                top_k=request.top_k,
                minimum_score=request.minimum_score,
                source_path=request.source_path,
            )
        except (CorpusVectorStoreError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.get("/rag/status")
    def local_rag_status() -> dict:
        return rag_status(configured_workspace_root)

    @application.get("/intelligence/components")
    def intelligence_components_endpoint() -> dict:
        return {"components": intelligence_components(), "count": len(intelligence_components())}

    @application.get("/intelligence/policy")
    def intelligence_policy_endpoint() -> dict:
        return model_use_policy()

    @application.get("/intelligence/workers")
    def intelligence_workers_endpoint() -> dict:
        roles = worker_roles()
        recent_jobs = job_queue.list_jobs(limit=20)
        return {
            "worker_roles": roles,
            "count": len(roles),
            "recent_jobs": [job.model_dump(mode="json") for job in recent_jobs],
        }

    @application.get("/intelligence/models")
    def intelligence_models_endpoint(limit: int = Query(default=10, ge=1, le=50)) -> dict:
        runs = repository.list_chat_runs(limit=limit)
        dashboard = build_intelligence_dashboard(chat_runs=runs, job_queue=job_queue, limit=limit)
        return dashboard["model_evaluation_summary"]

    @application.get("/intelligence/decision-traces")
    def intelligence_decision_traces_endpoint(limit: int = Query(default=10, ge=1, le=50)) -> dict:
        runs = repository.list_chat_runs(limit=limit)
        traces = decision_traces_from_chat_runs(runs)
        return {"items": traces, "count": len(traces)}

    @application.get("/intelligence/dashboard")
    def intelligence_dashboard_endpoint(limit: int = Query(default=10, ge=1, le=50)) -> dict:
        runs = repository.list_chat_runs(limit=limit)
        return build_intelligence_dashboard(chat_runs=runs, job_queue=job_queue, limit=limit)

    @application.post("/assignments/upload")
    async def assignment_upload(
        request: Request,
        filename: str = Query(..., min_length=1, max_length=255),
    ) -> dict:
        normalized_name = filename.replace("\\", "/")
        original_name = Path(normalized_name).name.strip()
        suffix = Path(original_name).suffix.lower()
        if suffix not in ASSIGNMENT_DOCUMENT_EXTENSIONS:
            supported = ", ".join(sorted(ASSIGNMENT_DOCUMENT_EXTENSIONS))
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported assignment document extension. Supported extensions: {supported}.",
            )

        raw_content_length = request.headers.get("content-length")
        if raw_content_length:
            try:
                content_length = int(raw_content_length)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length header.")
            if content_length > MAX_ASSIGNMENT_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Assignment document is too large. Maximum upload size is 10 MB.",
                )

        content = await request.body()
        if not content:
            raise HTTPException(status_code=400, detail="Assignment document is empty.")
        if len(content) > MAX_ASSIGNMENT_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Assignment document is too large. Maximum upload size is 10 MB.",
            )

        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original_name).stem)
        safe_stem = safe_stem.strip("._-") or "assignment"
        safe_filename = f"{safe_stem[:120]}{suffix}"
        upload_directory = (
            configured_workspace_root
            / "data"
            / "assignment_uploads"
            / str(uuid4())
        )
        upload_directory.mkdir(parents=True, exist_ok=False)
        upload_path = upload_directory / safe_filename
        upload_path.write_bytes(content)
        relative_path = upload_path.relative_to(configured_workspace_root).as_posix()
        return {
            "filename": safe_filename,
            "original_filename": original_name,
            "path": relative_path,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @application.get("/assignments/status")
    def assignment_status() -> dict:
        return {
            "status": "ready",
            "supported_extensions": sorted(ASSIGNMENT_DOCUMENT_EXTENSIONS),
            "supported_dataset_extensions": [".csv", ".txt", ".tsv"],
            "features": ["parse", "extract", "plan", "template_plan", "template_write"],
            "advisory_only": True,
            "tools_executed": False,
            "patches_applied": False,
            "runtime_authorized": False,
        }

    @application.post("/assignments/parse")
    def assignment_parse(request: AssignmentParseRequest) -> dict:
        audit_id = hashlib.sha256(str(request.path).encode("utf-8")).hexdigest()[:24]
        try:
            parsed = parse_assignment_document(_resolve_assignment_path(request.path))
        except FileNotFoundError as error:
            _stage0_audit("document", audit_id, "document_parse_failure", "not_found", {"error_type": type(error).__name__})
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            operation = "incomplete_document_parse" if getattr(error, "code", "") == "document_limit_exceeded" else "document_parse_failure"
            _stage0_audit("document", audit_id, operation, "rejected", {"error_code": getattr(error, "code", "invalid_document")})
            raise HTTPException(status_code=400, detail=str(error)) from error
        _stage0_audit("document", parsed.document_id, "document_parse_completion", "completed", {
            "schema_version": parsed.schema_version, "block_count": len(parsed.document_blocks),
            "source_id": parsed.document_blocks[0].source_span.source_id if parsed.document_blocks else None,
        })
        return parsed.model_dump(mode="json")

    @application.post("/assignments/extract")
    def assignment_extract(request: AssignmentExtractRequest) -> dict:
        try:
            brief = _assignment_brief_from_request(request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return brief.model_dump(mode="json")

    @application.post("/assignments/plan")
    def assignment_plan(request: AssignmentPlanRequest) -> dict:
        try:
            if request.brief is not None:
                brief = AssignmentBrief.model_validate(request.brief)
            else:
                brief = _assignment_brief_from_request(
                    AssignmentExtractRequest(path=request.path, text=request.text)
                )
            plan = build_assignment_plan(brief)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return plan.model_dump(mode="json")

    @application.post("/assignments/evidence/build")
    def assignment_evidence_build(request: AssignmentEvidenceBuildRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            evidence = build_evidence_checklist(brief)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return evidence.model_dump(mode="json")

    @application.post("/assignments/evidence/update-status")
    def assignment_evidence_update_status(request: AssignmentEvidenceUpdateStatusRequest) -> dict:
        try:
            checklist = AssignmentEvidenceChecklist.model_validate(request.checklist)
            updated = update_evidence_status(
                checklist,
                request.evidence_id,
                request.status,
                notes=request.notes,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return updated.model_dump(mode="json")

    @application.post("/assignments/report/draft")
    def assignment_report_draft(request: AssignmentReportDraftRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            plan = build_assignment_plan(brief) if request.plan is None else AssignmentPlan.model_validate(request.plan)
            evidence = (
                build_evidence_checklist(brief)
                if request.evidence is None
                else AssignmentEvidenceChecklist.model_validate(request.evidence)
            )
            draft = generate_report_draft(
                brief,
                plan=plan,
                evidence=evidence,
                project_metadata=request.project_metadata,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return draft.model_dump(mode="json")

    @application.post("/assignments/report/skeleton")
    def assignment_report_skeleton(request: AssignmentReportSkeletonRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            evidence = (
                build_evidence_checklist(brief)
                if request.evidence is None
                else AssignmentEvidenceChecklist.model_validate(request.evidence)
            )
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            skeleton = generate_report_skeleton(brief, dataset_profile=dataset_profile, evidence=evidence)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return skeleton.model_dump(mode="json")

    @application.post("/assignments/tasks/breakdown")
    def assignment_tasks_breakdown(request: AssignmentTaskBreakdownRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            evidence = (
                build_evidence_checklist(brief)
                if request.evidence is None
                else AssignmentEvidenceChecklist.model_validate(request.evidence)
            )
            breakdown = generate_task_breakdown(brief, evidence=evidence)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return breakdown.model_dump(mode="json")

    @application.post("/assignments/marking/check")
    def assignment_marking_check(request: AssignmentMarkingCheckRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            evidence = (
                None
                if request.evidence is None
                else AssignmentEvidenceChecklist.model_validate(request.evidence)
            )
            readiness = check_marking_readiness(brief, evidence)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"items": [item.model_dump(mode="json") for item in readiness]}

    @application.post("/assignments/copilot/run")
    def assignment_copilot_run(request: AssignmentCopilotRunRequest) -> dict:
        try:
            result = _run_assignment_copilot_request(request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/chat/assignments/analyze", response_model=ChatRunResponse)
    def chat_assignment_analyze(
        request: ChatAssignmentAnalyzeRequest,
    ) -> ChatRunResponse:
        try:
            result_model = _run_assignment_copilot_request(request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        result = result_model.model_dump(mode="json")
        created_at = datetime.now(timezone.utc)
        conversation_id = request.conversation_id or str(uuid4())
        action = _assignment_chat_action(result)
        run = ChatRunResponse(
            run_id=str(uuid4()),
            conversation_id=conversation_id,
            user_message=(
                request.user_message
                or "Read assignment"
            ),
            assistant_response=str(result.get("next_recommended_step") or ""),
            selected_specialist="assignment_copilot",
            intent="assignment_analysis",
            confidence=1.0,
            rag_used=False,
            rag_skip_reason="Assignment analysis uses the assignment copilot workflow.",
            rag_context_count=0,
            runtime_decision="workspace_action_approval_required",
            safety_decision="approval_required",
            used_real_slm=False,
            slm_provider="not_invoked",
            slm_fallback_reason="Assignment copilot used deterministic local analysis.",
            memory_used=False,
            memory_summary=None,
            created_at=created_at,
            trace_summary=[
                {
                    "phase": "assignment_analysis",
                    "title": "Assignment analysis persisted",
                    "detail": "Stored the structured assignment result and workspace action in chat history.",
                    "status": "passed",
                }
            ],
            action=action,
        )
        repository.store_chat_run(run)
        return run

    @application.post(
        "/chat/assignments/workspace/{action_id}/approve",
        response_model=ChatRunResponse,
    )
    def chat_assignment_workspace_approve(
        action_id: str,
        request: ChatAssignmentActionRequest,
    ) -> ChatRunResponse:
        _require_assignment_action_association(request.chat_run_id, action_id)
        run = repository.get_chat_run(request.chat_run_id)
        action = run.action or {}
        if action.get("status") != "awaiting_approval":
            raise HTTPException(
                status_code=400,
                detail="Assignment workspace action is not awaiting approval.",
            )

        technical = action.get("technical_details")
        if not isinstance(technical, dict):
            raise HTTPException(status_code=400, detail="Assignment action is malformed.")
        copilot_result = technical.get("copilot_result")
        workspace_action = technical.get("workspace_action")
        if not isinstance(copilot_result, dict) or not isinstance(workspace_action, dict):
            raise HTTPException(status_code=400, detail="Assignment action is malformed.")
        targets = workspace_action.get("targets")
        if not isinstance(targets, list) or not targets:
            raise HTTPException(status_code=400, detail="Assignment action has no workspace targets.")

        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "running",
                "error": None,
                "technical_details": {
                    "workspace_action": {
                        **workspace_action,
                        "status": "running",
                    }
                },
            },
        )

        results: list[dict] = []
        try:
            for target in targets:
                if not isinstance(target, dict):
                    raise ValueError("Workspace target is malformed.")
                generated = _generate_assignment_workspace_from_payload(
                    AssignmentWorkspaceGenerateRequest(
                        assignment_number=int(target["assignment_number"]),
                        workspace_path=str(target["workspace_path"]),
                        generation_mode=target.get("generation_mode", "mixed"),
                        overwrite=False,
                        copilot_result=copilot_result,
                    )
                )
                results.append(_sanitize_assignment_result(generated))
        except (TypeError, ValueError) as error:
            summary = (
                _assignment_workspace_summary(results)
                if results
                else None
            )
            repository.update_chat_run_action_for_id(
                request.chat_run_id,
                action_id,
                {
                    "status": "failed",
                    "result_summary": summary,
                    "error": str(error),
                    "technical_details": {
                        "workspace_action": {
                            **workspace_action,
                            "status": "failed",
                            "results": results,
                            "result_summary": summary,
                            "error": str(error),
                        }
                    },
                },
            )
            return repository.get_chat_run(request.chat_run_id)

        summary = _assignment_workspace_summary(results)
        status = "completed"
        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": status,
                "result_summary": summary,
                "error": None,
                "technical_details": {
                    "workspace_action": {
                        **workspace_action,
                        "status": status,
                        "results": results,
                        "result_summary": summary,
                        "final_workspace_location": (
                            results[0].get("workspace_path") if len(results) == 1 else None
                        ),
                    }
                },
            },
        )
        return repository.get_chat_run(request.chat_run_id)

    @application.post(
        "/chat/assignments/workspace/{action_id}/cancel",
        response_model=ChatRunResponse,
    )
    def chat_assignment_workspace_cancel(
        action_id: str,
        request: ChatAssignmentActionRequest,
    ) -> ChatRunResponse:
        _require_assignment_action_association(request.chat_run_id, action_id)
        run = repository.get_chat_run(request.chat_run_id)
        action = run.action or {}
        if action.get("status") != "awaiting_approval":
            raise HTTPException(
                status_code=400,
                detail="Assignment workspace action is not awaiting approval.",
            )
        technical = action.get("technical_details")
        workspace_action = (
            technical.get("workspace_action")
            if isinstance(technical, dict)
            else {}
        )
        if not isinstance(workspace_action, dict):
            workspace_action = {}
        summary = "Workspace creation cancelled. No files were written."
        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "cancelled",
                "result_summary": summary,
                "error": None,
                "technical_details": {
                    "workspace_action": {
                        **workspace_action,
                        "status": "cancelled",
                        "result_summary": summary,
                    }
                },
            },
        )
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/folders/request", response_model=ChatRunResponse)
    def chat_folder_request(request: ChatFolderRequest) -> ChatRunResponse:
        run = create_folder_chat_run(
            message=request.user_message or f"Use {request.path}",
            requested_path=request.path,
            conversation_id=request.conversation_id,
        )
        repository.store_chat_run(run)
        return run

    @application.post("/chat/folders/{action_id}/approve", response_model=ChatRunResponse)
    def chat_folder_approve(
        action_id: str,
        request: ChatFolderActionRequest,
    ) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, action_id)
        run = repository.get_chat_run(request.chat_run_id)
        action = run.action or {}
        if action.get("status") != "awaiting_approval":
            raise HTTPException(
                status_code=409,
                detail="Folder access action is not awaiting approval.",
            )
        folder_action = _folder_action_from_run(run)
        requested_path = str(folder_action.get("requested_path") or "")

        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "scanning",
                "error": None,
                "technical_details": {
                    "folder_action": {
                        **folder_action,
                        "status": "scanning",
                        "error": None,
                    }
                },
            },
        )

        try:
            approved_root = validate_folder_root(requested_path)
            scan = build_inventory(approved_root)
        except FileNotFoundError as error:
            _record_folder_failure(request.chat_run_id, action_id, folder_action, str(error))
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (FolderScanError, OSError) as error:
            _record_folder_failure(request.chat_run_id, action_id, folder_action, str(error))
            raise HTTPException(status_code=400, detail=str(error)) from error

        inventory = scan.get("inventory") if isinstance(scan.get("inventory"), list) else []
        diff = diff_inventories([], inventory)
        summary = _folder_result_summary(scan)
        completed_folder_action = {
            **folder_action,
            "status": "completed",
            "display_path": scan.get("root_display_name") or folder_action.get("display_path"),
            "approved_root": str(approved_root),
            "root_fingerprint": project_root_fingerprint(approved_root),
            "approved_root_display": scan.get("root_display_name"),
            "inventory": inventory,
            "summary": scan.get("summary") or {},
            "warnings": scan.get("warnings") or [],
            "diff": diff,
            "scan_count": 1,
            "last_scanned_at": scan.get("scanned_at"),
            "limits": scan.get("limits") or {},
            "result_summary": summary,
            "error": None,
        }
        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "completed",
                "approval_required": False,
                "result_summary": summary,
                "error": None,
                "technical_details": {"folder_action": completed_folder_action},
            },
        )
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/folders/{action_id}/cancel", response_model=ChatRunResponse)
    def chat_folder_cancel(
        action_id: str,
        request: ChatFolderActionRequest,
    ) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, action_id)
        run = repository.get_chat_run(request.chat_run_id)
        action = run.action or {}
        if action.get("status") != "awaiting_approval":
            raise HTTPException(
                status_code=409,
                detail="Folder access action is not awaiting approval.",
            )
        folder_action = _folder_action_from_run(run)
        summary = "Folder access cancelled. No folder was scanned."
        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "cancelled",
                "approval_required": False,
                "result_summary": summary,
                "error": None,
                "technical_details": {
                    "folder_action": {
                        **folder_action,
                        "status": "cancelled",
                        "result_summary": summary,
                        "error": None,
                    }
                },
            },
        )
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/folders/{action_id}/rescan", response_model=ChatRunResponse)
    def chat_folder_rescan(
        action_id: str,
        request: ChatFolderActionRequest,
    ) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, action_id)
        run = repository.get_chat_run(request.chat_run_id)
        action = run.action or {}
        if action.get("status") != "completed":
            raise HTTPException(
                status_code=409,
                detail="Folder access action must be completed before rescan.",
            )
        folder_action = _folder_action_from_run(run)
        approved_root = str(folder_action.get("approved_root") or "")
        previous_inventory = folder_action.get("inventory")
        if not isinstance(previous_inventory, list):
            previous_inventory = []

        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "scanning",
                "error": None,
                "technical_details": {
                    "folder_action": {
                        **folder_action,
                        "status": "scanning",
                        "error": None,
                    }
                },
            },
        )

        try:
            expected_fingerprint = str(folder_action.get("root_fingerprint") or "")
            root = (
                validate_root_identity(approved_root, expected_fingerprint)
                if expected_fingerprint
                else validate_folder_root(approved_root)
            )
            scan = build_inventory(root)
        except FileNotFoundError as error:
            _record_folder_failure(request.chat_run_id, action_id, folder_action, str(error))
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (FolderScanError, OSError) as error:
            _record_folder_failure(request.chat_run_id, action_id, folder_action, str(error))
            raise HTTPException(status_code=400, detail=str(error)) from error

        inventory = scan.get("inventory") if isinstance(scan.get("inventory"), list) else []
        diff = diff_inventories(previous_inventory, inventory)
        summary = _folder_result_summary(scan)
        scan_count = int(folder_action.get("scan_count") or 0) + 1
        repository.update_chat_run_action_for_id(
            request.chat_run_id,
            action_id,
            {
                "status": "completed",
                "approval_required": False,
                "result_summary": summary,
                "error": None,
                "technical_details": {
                    "folder_action": {
                        **folder_action,
                        "status": "completed",
                        "display_path": scan.get("root_display_name") or folder_action.get("display_path"),
                        "approved_root": str(root),
                        "approved_root_display": scan.get("root_display_name"),
                        "inventory": inventory,
                        "summary": scan.get("summary") or {},
                        "warnings": scan.get("warnings") or [],
                        "diff": diff,
                        "scan_count": scan_count,
                        "last_scanned_at": scan.get("scanned_at"),
                        "limits": scan.get("limits") or {},
                        "result_summary": summary,
                        "error": None,
                    }
                },
            },
        )
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/client-engagements", response_model=ChatRunResponse)
    def chat_engagement_create(request: EngagementCreateRequest) -> ChatRunResponse:
        conversation_id = request.conversation_id or uuid4().hex
        access = _optional_project_access(conversation_id)
        with engagement_lock:
            try:
                engagement = engagement_service.create(
                    conversation_id=conversation_id, original_request=request.client_request,
                    user_id=request.user_id, display_name=request.display_name,
                    organization=request.organization,
                    folder_root=str(access["approved_root"]) if access else None,
                    folder_access_id=str(access["action_id"]) if access else None,
                    constraints=request.constraints, idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        run = build_engagement_chat_run(engagement, message=request.client_request)
        repository.store_chat_run(run)
        return run

    @application.get("/chat/client-engagements/{engagement_id}")
    def chat_engagement_get(engagement_id: str, conversation_id: str = Query(..., min_length=1, max_length=128)) -> dict:
        engagement = _validated_engagement(engagement_id, conversation_id)
        return public_engagement(engagement).model_dump(mode="json")

    @application.get("/chat/client-engagements/{engagement_id}/history")
    def chat_engagement_history(engagement_id: str, conversation_id: str = Query(..., min_length=1, max_length=128), limit: int = Query(default=50, ge=1, le=100)) -> dict:
        _validated_engagement(engagement_id, conversation_id)
        items = repository.list_client_engagement_audit_events(engagement_id, limit=limit)
        return {"engagement_id": engagement_id, "items": items, "count": len(items)}

    @application.post("/chat/client-engagements/{engagement_id}/analyze", response_model=ChatRunResponse)
    def chat_engagement_analyze(engagement_id: str, request: EngagementVersionRequest) -> ChatRunResponse:
        current = _validated_engagement(engagement_id, request.conversation_id)
        access = _optional_project_access(request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.analyze(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    folder_root=str(access["approved_root"]) if access else None,
                    idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Reanalyze this engagement.")

    @application.post("/chat/client-engagements/{engagement_id}/clarifications", response_model=ChatRunResponse)
    def chat_engagement_answers(engagement_id: str, request: EngagementAnswerRequest) -> ChatRunResponse:
        _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.submit_answers(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    answers=request.answers, answered_by=request.answered_by,
                    use_reasonable_assumptions=request.use_reasonable_assumptions,
                    idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Submit engagement clarification answers.")

    @application.post("/chat/client-engagements/{engagement_id}/scope", response_model=ChatRunResponse)
    def chat_engagement_scope(engagement_id: str, request: EngagementVersionRequest) -> ChatRunResponse:
        _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.generate_scope(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Regenerate the client scope.")

    @application.post("/chat/client-engagements/{engagement_id}/scope/approve", response_model=ChatRunResponse)
    def chat_engagement_approve(engagement_id: str, request: EngagementApprovalRequest) -> ChatRunResponse:
        before = _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.approve_scope(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    revision_id=request.revision_id, scope_hash=request.scope_hash,
                    approving_user=request.approving_user, idempotency_key=request.idempotency_key,
                )
                if before.get("project_launch") and before.get("current_scope", {}).get("revision_id") == request.revision_id:
                    _notify_delivery_scope_change(before, stored)
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Approve the exact displayed client scope.")

    @application.post("/chat/client-engagements/{engagement_id}/scope/reject", response_model=ChatRunResponse)
    def chat_engagement_reject(engagement_id: str, request: EngagementRejectionRequest) -> ChatRunResponse:
        _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.reject_scope(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    revision_id=request.revision_id, reason=request.reason,
                    rejecting_user=request.rejecting_user, idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Reject this client scope.")

    @application.post("/chat/client-engagements/{engagement_id}/launch", response_model=ChatRunResponse)
    def chat_engagement_launch(engagement_id: str, request: EngagementVersionRequest) -> ChatRunResponse:
        current = _validated_engagement(engagement_id, request.conversation_id)
        access = _completed_project_access(request.conversation_id)
        if current.get("folder_access_id") and current.get("folder_access_id") != access.get("action_id"):
            raise HTTPException(status_code=409, detail={"code": "workspace_mismatch", "message": "The approved scope belongs to a different folder authorization."})
        with engagement_lock, delivery_lock:
            try:
                stored, _delivery = engagement_service.launch(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    launch_stage9=lambda engagement, revision: _launch_engagement_delivery(engagement, revision, access),
                    idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Launch the approved scope as a Stage 9 project.")

    @application.post("/chat/client-engagements/{engagement_id}/scope-changes", response_model=ChatRunResponse)
    def chat_engagement_scope_change(engagement_id: str, request: EngagementScopeChangeApiRequest) -> ChatRunResponse:
        _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.request_scope_change(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    requested_change=request.requested_change, requested_by=request.requested_by,
                    idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, request.requested_change)

    @application.post("/chat/client-engagements/{engagement_id}/cancel", response_model=ChatRunResponse)
    def chat_engagement_cancel(engagement_id: str, request: EngagementVersionRequest) -> ChatRunResponse:
        _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.cancel(
                    engagement_id=engagement_id, expected_version=request.expected_state_version,
                    actor="local-user", idempotency_key=request.idempotency_key,
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Cancel this client engagement.")

    @application.post("/chat/client-engagements/{engagement_id}/recover", response_model=ChatRunResponse)
    def chat_engagement_recover(engagement_id: str, request: EngagementVersionRequest) -> ChatRunResponse:
        _validated_engagement(engagement_id, request.conversation_id)
        with engagement_lock:
            try:
                stored = engagement_service.recover(engagement_id=engagement_id, conversation_id=request.conversation_id)
            except EngagementError as error:
                raise _engagement_http_error(error) from error
        return _engagement_run(stored, "Recover this client engagement.")

    @application.post("/chat/projects/deliveries", response_model=ChatRunResponse)
    def chat_project_delivery_start(request: ProjectDeliveryStartRequest) -> ChatRunResponse:
        access = _completed_project_access(request.conversation_id)
        return _start_project_delivery(request.user_request, request.conversation_id, access, persist_run=True)

    @application.get("/chat/projects/deliveries/{delivery_job_id}")
    def chat_project_delivery_get(delivery_job_id: str) -> dict:
        job, _access = _read_project_delivery(delivery_job_id)
        payload = _public_read_delivery(job)
        payload["canonical_project"] = payload.get("project_control")
        payload["coordinator_intents"] = [
            item.model_dump(mode="json")
            for item in project_coordinator.list_for_project(delivery_job_id)
        ]
        return payload

    @application.get("/chat/conversations/{conversation_id}/project-deliveries")
    def chat_project_deliveries_list(conversation_id: str) -> dict:
        jobs = repository.list_project_delivery_jobs_for_conversation(conversation_id)
        canonical = [
            _read_project_delivery(str(job["delivery_job_id"]), conversation_id=conversation_id)[0]
            for job in jobs
        ]
        items: list[dict] = []
        for job in canonical:
            payload = _public_read_delivery(job)
            payload["canonical_project"] = payload.get("project_control")
            payload["coordinator_intents"] = [
                item.model_dump(mode="json")
                for item in project_coordinator.list_for_project(str(job["delivery_job_id"]))
            ]
            items.append(payload)
        return {"items": items, "count": len(items)}

    @application.get("/chat/projects/deliveries/{delivery_job_id}/specification")
    def chat_project_delivery_specification(delivery_job_id: str) -> dict:
        job, _access = _read_project_delivery(delivery_job_id)
        return {"specification": job["specification"], "revisions": job.get("specification_revisions", [])}

    @application.get("/chat/projects/deliveries/{delivery_job_id}/plan")
    def chat_project_delivery_plan(delivery_job_id: str) -> dict:
        job, _access = _read_project_delivery(delivery_job_id)
        return {"plan": job.get("plan"), "approval": job.get("plan_approval"), "revisions": job.get("plan_revisions", [])}

    @application.post("/chat/projects/deliveries/{delivery_job_id}/clarify", response_model=ChatRunResponse)
    def chat_project_delivery_clarify(delivery_job_id: str, request: ProjectDeliveryClarificationRequest) -> ChatRunResponse:
        current, access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        _validate_delivery_action_binding(current, request)
        try:
            updated = submit_delivery_clarification(current, answer=request.answer, root=access["approved_root"])
        except ProjectDeliveryError as error:
            raise _delivery_http_error(error) from error
        stored = _save_delivery_transition(current, updated, "clarification_response", "completed", {"idempotency_key": request.idempotency_key})
        run = build_delivery_chat_run(stored, message=request.answer)
        repository.store_chat_run(run)
        _sync_delivery_action(stored)
        return run

    @application.post("/chat/projects/deliveries/{delivery_job_id}/plan/approve", response_model=ChatRunResponse)
    def chat_project_delivery_plan_approve(delivery_job_id: str, request: ProjectDeliveryHashRequest) -> ChatRunResponse:
        current, access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        # A completed exact retry must reach the canonical idempotency record
        # before live-state validation. The adapter/control plane still verify
        # the full normalized command hash and reject any changed binding.
        if not project_control.has_idempotency_key(
            delivery_job_id, str(request.idempotency_key)
        ):
            _validate_delivery_action_binding(current, request)
        canonical_preapplied = False
        try:
            delivery_control.approve_plan_bound(
                current, access["approved_root"], plan_hash=request.immutable_hash,
                idempotency_key=str(request.idempotency_key),
                expected_state_version=request.expected_state_version,
                plan_revision_id=request.plan_revision_id,
                scope_revision_id=request.scope_revision_id,
                manifest_hash=request.manifest_hash,
                artifact_id=request.artifact_id,
                artifact_type=request.artifact_type,
                artifact_hash=request.artifact_hash,
                artifact_binding_hash=request.artifact_binding_hash,
            )
            canonical_preapplied = True
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        try:
            updated = approve_delivery_plan(current, plan_hash=request.immutable_hash, root=access["approved_root"])
        except ProjectDeliveryError as error:
            operation = "legacy_approval_reapproval_required" if error.code == "migration_reapproval_required" else "plan_approval_rejected"
            _delivery_audit(current, operation, "rejected", {"error_code": error.code})
            raise _delivery_http_error(error) from error
        if updated is current:
            return build_delivery_chat_run(current, message="Approve the current delivery plan.", response="This exact plan was already approved. No patch or command was started.")
        stored = _save_delivery_transition(current, updated, "plan_approval_granted", "approved", {
            "plan_hash": request.immutable_hash, "idempotency_key": request.idempotency_key,
            "canonical_preapplied": canonical_preapplied,
        })
        run = build_delivery_chat_run(stored, message="Approve the current delivery plan.", response="Plan approved. This authorizes only work-unit preparation; no files changed and no command ran.")
        repository.store_chat_run(run)
        _sync_delivery_action(stored)
        return run

    @application.post(
        "/chat/projects/deliveries/{delivery_job_id}/import-historical",
        response_model=CanonicalProjectResponse,
    )
    def chat_project_delivery_import_historical(
        delivery_job_id: str, request: HistoricalProjectImportRequest
    ) -> CanonicalProjectResponse:
        if not delivery_control.classification.is_historical_read_only(delivery_job_id):
            raise HTTPException(status_code=409, detail={
                "code": "not_historical",
                "message": "Only historical read-only records can be explicitly imported and reapproved.",
            })
        if request.historical_source_id != delivery_job_id:
            raise HTTPException(status_code=409, detail={
                "code": "historical_source_mismatch",
                "message": "The import request is bound to a different historical record.",
            })
        job, access = _read_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        if (
            request.workspace_id != str(access["action_id"])
            or request.repository_root_fingerprint != str(access["root_fingerprint"])
            or request.actor_id != "local-user"
        ):
            raise HTTPException(status_code=409, detail={
                "code": "workspace_mismatch",
                "message": "The historical import identity does not match the approved workspace.",
            })
        folder_authority = {
            "status": "completed",
            "action_id": access["action_id"],
            "conversation_id": str(job.get("conversation_id") or ""),
            "workspace_id": access["action_id"],
            "repository_root_fingerprint": access["root_fingerprint"],
        }
        try:
            imported = canonical_project_service.import_historical_record(
                historical_job=job,
                conversation_id=str(job.get("conversation_id") or ""),
                workspace_id=str(access["action_id"]),
                repository_root=access["approved_root"],
                repository_root_fingerprint=str(access["root_fingerprint"]),
                folder_authority=folder_authority,
                idempotency_key=request.idempotency_key,
                actor_id=request.actor_id,
            )
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        return build_canonical_project_response(
            canonical_project_service, imported.project_run_id, coordinator=project_coordinator
        )

    @application.post("/chat/projects/deliveries/{delivery_job_id}/prepare", response_model=ChatRunResponse)
    def chat_project_delivery_prepare(delivery_job_id: str, request: ProjectJobActionRequest) -> ChatRunResponse:
        current, access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        _validate_delivery_action_binding(current, request)
        active_prepare_intents = [
            item for item in project_coordinator.list_for_project(delivery_job_id)
            if item.intent_type.value == "prepare_work_unit"
            and item.status.value in {"pending", "claimed"}
        ]
        if (
            not active_prepare_intents
            or request.coordinator_intent_id
            != active_prepare_intents[-1].coordinator_intent_id
        ):
            raise HTTPException(status_code=409, detail={
                "schema_version": "astra.project-control.error.v1",
                "code": "stale_binding",
                "message": "The preparation action is not bound to the current coordinator intent.",
            })
        try:
            with delivery_lock:
                current = repository.get_project_delivery_job(delivery_job_id)
                activated = activate_next_work_unit(current, root=access["approved_root"])
                shadow = repository.get_project_job(str(activated["project_job_id"]))
                unit = next(item for item in activated["plan"]["work_units"] if item["work_unit_id"] == activated["active_work_unit_id"])
                shadow_for_work = {
                    **shadow, "status": "planned", "user_task": unit["objective"],
                    "objective": unit["objective"], "relevant_paths": unit["expected_files"],
                    "analysis_id": activated["analysis_id"], "analysis_index": activated["analysis_index"],
                    "analysis": activated["analysis"],
                }
                bundle = prepare_job_patch_bundle(
                    access["approved_root"], shadow_for_work, model_gateway=None,
                )
                proposal = create_patch_proposal(
                    root=access["approved_root"], conversation_id=current["conversation_id"],
                    folder_access_id=access["action_id"], user_request=unit["objective"],
                    changes=bundle["changes"], files_inspected=[
                        change["path"] for change in bundle["changes"] if change["operation"] != "create"
                    ], validation_plan=unit.get("expected_validation_commands") or [],
                    job_id=shadow["job_id"], analysis_context=bundle.get("analysis_context"),
                )
                proposal["delivery_job_id"] = delivery_job_id
                proposal["work_unit_id"] = unit["work_unit_id"]
                linked = link_patch_preview(activated, patch=proposal)
                if linked.get("status") == DeliveryStatus.REPLANNING.value:
                    _save_delivery_transition(current, linked, "scope_change_detection", "replanning_required")
                    raise ProjectDeliveryError("The prepared patch exceeded the approved work-unit scope; replanning is required.", code="scope_change")
                repository.store_project_patch(proposal)
                shadow_updated = {
                    **shadow, "status": "patch_proposed", "analysis_id": activated["analysis_id"],
                    "analysis_index": activated["analysis_index"], "analysis": activated["analysis"],
                    "patch_ids": [*shadow.get("patch_ids", []), proposal["patch_id"]],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                if not repository.transition_project_job(shadow_updated, expected_statuses={str(shadow["status"])}):
                    raise ProjectDeliveryError("The execution bridge changed concurrently.", code="conflict")
                stored = _save_delivery_transition(current, linked, "patch_preview", "awaiting_approval", {"patch_id": proposal["patch_id"], "work_unit_id": unit["work_unit_id"], "idempotency_key": request.idempotency_key, "coordinator_intent_id": request.coordinator_intent_id})
        except (ProjectDeliveryError, ProjectJobError, ProjectAnalysisError, ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            if isinstance(error, ProjectDeliveryError):
                raise _delivery_http_error(error) from error
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        _sync_delivery_action(stored)
        run = _project_patch_run(proposal)
        repository.store_chat_run(run)
        return run

    @application.post("/chat/projects/deliveries/{delivery_job_id}/verification", response_model=ChatRunResponse)
    def chat_project_delivery_verification(delivery_job_id: str, request: ProjectDeliveryVerificationRequest) -> ChatRunResponse:
        current, access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        _validate_delivery_action_binding(current, request)
        if current.get("status") != DeliveryStatus.PATCH_APPLIED.value:
            raise HTTPException(status_code=409, detail={"code": "patch_not_applied", "message": "Apply the approved work-unit patch before requesting verification."})
        shadow = repository.get_project_job(str(current["project_job_id"]))
        criteria = list((current.get("specification") or {}).get("acceptance_criteria") or [])
        criterion = next((item for item in criteria if item.get("criterion_id") == request.criterion_id), None)
        if criterion is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_criterion", "message": "Acceptance criterion not found."})
        mode = VerificationMode(str(criterion["verification_mode"]))
        if mode in {VerificationMode.STRUCTURAL, VerificationMode.FILE_PRESENCE, VerificationMode.CONFIGURATION, VerificationMode.EXACT_ASSERTION, VerificationMode.MANUAL}:
            _delivery_audit(current, "verifier_start", "started", {"criterion_id": request.criterion_id, "verifier_type": mode.value})
            try:
                verifier = run_deterministic_verifier(
                    current, root=access["approved_root"], criterion_id=request.criterion_id,
                )
                verification_state = {
                    VerifierOutcome.PASSED: VerificationState.SATISFIED,
                    VerifierOutcome.FAILED: VerificationState.FAILED,
                    VerifierOutcome.INCONCLUSIVE: VerificationState.BLOCKED,
                    VerifierOutcome.MANUAL_REQUIRED: VerificationState.MANUAL,
                }[verifier.outcome]
                updated = record_delivery_verification(
                    current, work_unit_id=str(current["active_work_unit_id"]), criterion_id=request.criterion_id,
                    state=verification_state, method=mode,
                    evidence_references=[verifier.verifier_result_id],
                    relevant_file_hashes={path: digest for ref in current.get("patch_references", []) if ref.get("status") == "applied" for path, digest in ref.get("after_hashes", {}).items()},
                    structural_analysis_references=[verifier.verifier_result_id] if mode == VerificationMode.STRUCTURAL else [],
                    failure_explanation=verifier.failure_reason,
                    verifier_result=verifier,
                )
            except (ProjectDeliveryError, ProjectVerifierError, ProjectManifestError) as error:
                operation = "stale_verifier_rejected" if getattr(error, "code", "") == "stale_verifier_result" else "verifier_completion"
                _delivery_audit(current, operation, "rejected", {"criterion_id": request.criterion_id, "error_code": getattr(error, "code", "verifier_error")})
                raise _delivery_http_error(error) from error
            stored = _save_delivery_transition(current, updated, "verifier_completion", verifier.outcome.value, {"criterion_id": request.criterion_id, "verifier_result_id": verifier.verifier_result_id, "idempotency_key": request.idempotency_key})
            _sync_delivery_action(stored)
            response = {
                VerifierOutcome.PASSED: "A fresh deterministic verifier passed the criterion with typed evidence.",
                VerifierOutcome.FAILED: "Deterministic verification failed. Review the recorded checks before repair.",
                VerifierOutcome.INCONCLUSIVE: "Verification was inconclusive because a required deterministic check could not be completed.",
                VerifierOutcome.MANUAL_REQUIRED: "This criterion requires manual validation and was not passed automatically.",
            }[verifier.outcome]
            run = build_delivery_chat_run(stored, message="Verify the acceptance criterion.", response=response)
            repository.store_chat_run(run)
            return run
        if int(current.get("budgets", {}).get("command_executions", 0)) >= int(current.get("limits", {}).get("max_command_executions", 15)):
            limited = json.loads(json.dumps(current))
            limited.update({
                "status": DeliveryStatus.LIMIT_REACHED.value,
                "last_error": {"code": "command_limit", "message": "The configured Stage 9 command-execution limit was reached."},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            stored = _save_delivery_transition(current, limited, "limit_reached", "command_limit")
            _sync_delivery_action(stored)
            raise HTTPException(status_code=409, detail={"code": "command_limit", "message": "The configured Stage 9 command-execution limit was reached."})
        plans = list(shadow.get("validation_plan") or [])
        if not plans:
            raise HTTPException(status_code=409, detail={"code": "no_allowlisted_command", "message": "No allowlisted verification command was detected."})
        selected = plans[0]
        try:
            run = _plan_project_command_run(
                conversation_id=current["conversation_id"], access=access,
                message=f"Verify {request.criterion_id} for the approved delivery work unit.",
                action=str(selected.get("action") or "pytest"), target=selected.get("target"),
                expected_result=str(selected.get("expected_result") or "The configured validation exits successfully."),
                timeout_seconds=120, job_id=str(shadow["job_id"]),
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        plan = run.action["technical_details"]["command_plan"] if run.action else {}
        if run.action:
            project_scope = dict(run.action["technical_details"].get("project_scope") or {})
            run.action["technical_details"]["project_scope"] = {**project_scope, "delivery_job_id": delivery_job_id}
        plan_id = str(plan.get("plan_id") or "")
        if shadow.get("status") == "patch_approved":
            implementing = {**shadow, "status": "implementing", "updated_at": datetime.now(timezone.utc).isoformat()}
            if not repository.transition_project_job(implementing, expected_statuses={"patch_approved"}):
                raise HTTPException(status_code=409, detail={"code": "conflict", "message": "The canonical patch projection changed concurrently."})
            shadow = implementing
        shadow_updated = {**shadow, "status": "validating", "command_plan_ids": [*shadow.get("command_plan_ids", []), plan_id], "updated_at": datetime.now(timezone.utc).isoformat()}
        if not repository.transition_project_job(
            shadow_updated,
            expected_statuses={"implementing"},
        ):
            raise HTTPException(status_code=409, detail={"code": "conflict", "message": "Verification planning was already started."})
        updated = json.loads(json.dumps(current))
        updated["command_references"] = [*updated.get("command_references", []), {
            "plan_id": plan_id, "work_unit_id": current["active_work_unit_id"], "criterion_id": request.criterion_id,
            "method": mode.value, "action": plan.get("action"), "status": "awaiting_approval", "created_at": datetime.now(timezone.utc).isoformat(),
        }]
        updated["status"] = DeliveryStatus.AWAITING_COMMAND.value
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        stored = _save_delivery_transition(current, updated, "command_plan", "awaiting_approval", {"plan_id": plan_id, "criterion_id": request.criterion_id, "idempotency_key": request.idempotency_key, "coordinator_intent_id": request.coordinator_intent_id})
        repository.store_chat_run(run)
        _sync_delivery_action(stored)
        return run

    @application.post("/chat/projects/deliveries/{delivery_job_id}/handoff", response_model=ChatRunResponse)
    def chat_project_delivery_handoff(delivery_job_id: str, request: ProjectJobActionRequest) -> ChatRunResponse:
        current, access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        _validate_delivery_action_binding(current, request)
        try:
            updated = generate_handoff(current, root=access["approved_root"])
        except (ProjectDeliveryError, ProjectManifestError) as error:
            if getattr(error, "code", "") == "stale_verifier_result":
                _delivery_audit(current, "stale_verifier_rejected", "rejected", {"context": "handoff"})
            raise _delivery_http_error(error) from error
        stored = _save_delivery_transition(current, updated, "handoff_generation", str((updated.get("handoff") or {}).get("completion_status") or "recorded"), {"idempotency_key": request.idempotency_key})
        run = build_delivery_chat_run(stored, message="Prepare the client handoff.")
        repository.store_chat_run(run)
        _sync_delivery_action(stored)
        return run

    @application.post("/chat/projects/deliveries/{delivery_job_id}/scope-revision", response_model=ChatRunResponse)
    def chat_project_delivery_scope_revision(delivery_job_id: str, request: ProjectDeliveryClarificationRequest) -> ChatRunResponse:
        current, access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        _validate_delivery_action_binding(current, request)
        try:
            updated = revise_delivery_scope(current, root=access["approved_root"], explanation=request.answer)
        except ProjectDeliveryError as error:
            raise _delivery_http_error(error) from error
        stored = _save_delivery_transition(current, updated, "scope_revision", "awaiting_plan_approval", {"idempotency_key": request.idempotency_key})
        _delivery_audit(stored, "plan_revision_superseded", "completed", {
            "superseded_revision_id": (stored.get("plan_revision") or {}).get("supersedes_revision_id"),
            "new_revision_id": (stored.get("plan_revision") or {}).get("plan_revision_id"),
        })
        _delivery_audit(stored, "plan_revision_created", "completed", {
            "plan_revision_id": (stored.get("plan_revision") or {}).get("plan_revision_id"),
        })
        run = build_delivery_chat_run(stored, message=request.answer, response="The material scope change produced a new immutable specification and plan. Previous approvals remain invalid.")
        repository.store_chat_run(run)
        _sync_delivery_action(stored)
        return run

    @application.post("/chat/projects/deliveries/{delivery_job_id}/cancel")
    def chat_project_delivery_cancel(delivery_job_id: str, request: ProjectJobActionRequest) -> dict:
        if cancellation_dispatcher is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "project_reconciliation_disabled",
                    "message": "Canonical cancellation recovery is disabled; no cancellation was submitted.",
                },
            )
        current, _access = _validated_project_delivery(delivery_job_id, conversation_id=request.conversation_id)
        _validate_delivery_action_binding(current, request)
        run = project_control.get_project(delivery_job_id)
        try:
            result = project_control.execute(ProjectCommand(
                command_type=ProjectCommandType.CANCEL_PROJECT,
                project_run_id=run.project_run_id,
                conversation_id=run.conversation_id,
                workspace_id=run.workspace_id,
                repository_root=run.repository_root,
                repository_root_fingerprint=run.repository_root_fingerprint,
                actor_id=run.actor_id,
                expected_state_version=request.expected_state_version or run.state_version,
                idempotency_key=request.idempotency_key or f"cancel-project:{run.project_run_id}",
                plan_revision_id=run.current_plan_revision_id,
                scope_revision_id=run.current_scope_revision_id,
                manifest_hash=run.current_manifest_hash,
                authority_scope={"project_run_id": run.project_run_id},
                payload={"reason": "Cancelled through the project delivery endpoint."},
            ))
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        try:
            project_projection.rebuild_project(delivery_job_id)
        except (LookupError, RuntimeError, ValueError):
            pass
        payload = public_delivery_job(repository.get_project_delivery_job(delivery_job_id))
        payload["project_control"] = result.read_model
        payload["canonical_project"] = result.read_model
        return payload

    @application.post("/chat/projects/patches/propose", response_model=ChatRunResponse)
    def chat_project_patch_propose(request: ProjectPatchProposalRequest) -> ChatRunResponse:
        access = _completed_project_access(request.conversation_id)
        try:
            proposal = create_patch_proposal(
                root=access["approved_root"],
                conversation_id=request.conversation_id,
                folder_access_id=access["action_id"],
                user_request=request.user_request,
                changes=[change.model_dump() for change in request.changes],
                files_inspected=request.files_inspected,
                validation_plan=request.validation_plan,
            )
        except (ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        repository.store_project_patch(proposal)
        run = _project_patch_run(proposal)
        repository.store_chat_run(run)
        audit_event(
            repository, conversation_id=request.conversation_id,
            folder_access_id=access["action_id"], patch_id=proposal["patch_id"],
            operation="patch_proposed", status="proposed",
            metadata={"relative_paths": proposal["file_set"], "file_count": len(proposal["file_set"]), "additions": proposal["additions"], "deletions": proposal["deletions"]},
        )
        return run

    @application.get("/chat/projects/jobs/{job_id}")
    def chat_project_job_get(job_id: str) -> dict:
        job, _access = _validated_project_job(job_id)
        return public_project_job(job)

    @application.get("/chat/conversations/{conversation_id}/project-jobs")
    def chat_project_jobs_list(conversation_id: str) -> dict:
        jobs = repository.list_project_jobs_for_conversation(conversation_id)
        for job in jobs:
            _validated_project_job(str(job["job_id"]), conversation_id=conversation_id)
        return {"items": [public_project_job(job) for job in jobs], "count": len(jobs)}

    @application.get("/chat/projects/jobs/{job_id}/analysis")
    def chat_project_job_analysis(job_id: str) -> dict:
        job, _access = _validated_project_job(job_id)
        index = _validated_project_analysis(job)
        return {"index": public_index(index), "analysis": job.get("analysis") or {}}

    @application.get("/chat/projects/jobs/{job_id}/synthesis-attempts")
    def chat_project_job_synthesis_attempts(job_id: str) -> dict:
        job, _access = _validated_project_job(job_id)
        attempts = repository.list_project_synthesis_attempts_for_job(job_id)
        safe = [{key: value for key, value in attempt.items() if key not in {"raw_request", "raw_response", "evidence"}} for attempt in attempts]
        return {"job_id": job["job_id"], "items": safe, "count": len(safe)}

    @application.get("/chat/projects/jobs/{job_id}/failure-evidence")
    def chat_project_job_failure_evidence(job_id: str) -> dict:
        job, _access = _validated_project_job(job_id)
        values = repository.list_project_failure_evidence_for_job(job_id)
        safe = [{key: value for key, value in item.items() if key not in {"stdout_summary", "stderr_summary"}} for item in values]
        return {"job_id": job["job_id"], "items": safe, "count": len(safe)}

    @application.get("/chat/projects/jobs/{job_id}/diagnoses")
    def chat_project_job_diagnoses(job_id: str) -> dict:
        job, _access = _validated_project_job(job_id)
        values = repository.list_project_diagnoses_for_job(job_id)
        safe = [{key: value for key, value in item.items() if key not in {"raw_request", "raw_response", "source_excerpts", "model_failure_data"}} for item in values]
        return {"job_id": job["job_id"], "items": safe, "count": len(safe)}

    @application.get("/chat/projects/jobs/{job_id}/repair-cycles")
    def chat_project_job_repair_cycles(job_id: str) -> dict:
        job, _access = _validated_project_job(job_id)
        values = repository.list_project_repair_cycles_for_job(job_id)
        return {"job_id": job["job_id"], "items": values, "count": len(values)}

    @application.post("/chat/projects/jobs/{job_id}/analysis/refresh")
    def chat_project_job_analysis_refresh(job_id: str, request: ProjectJobActionRequest) -> dict:
        job, access = _validated_project_job(job_id, conversation_id=request.conversation_id)
        previous = _validated_project_analysis(job)
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, operation="structure_analysis_started", status="running",
            metadata={"analysis_id": previous.get("analysis_id"), "index_version": previous.get("index_version")},
        )
        try:
            index = build_project_index(
                access["approved_root"], conversation_id=job["conversation_id"],
                folder_access_id=access["action_id"], job_id=job_id, previous=previous,
            )
            analysis = build_analysis_plan(index, str(job.get("user_task") or ""), relevant_paths=list(job.get("relevant_paths") or []))
        except (ProjectAnalysisError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            audit_event(
                repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                job_id=job_id, operation="structure_analysis_completed", status="failed",
                metadata={"reason": _controlled_project_error(error)},
            )
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        repository.store_project_analysis(index)
        updated = {**job, "analysis_id": index["analysis_id"], "analysis_index": index, "analysis": analysis, "updated_at": datetime.now(timezone.utc).isoformat()}
        repository.update_project_job(updated)
        _sync_project_job_action(updated)
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, operation="structure_analysis_completed", status="completed",
            metadata=analysis_audit_metadata(index, include_relationships=True),
        )
        return {"index": public_index(index), "analysis": analysis}

    @application.post("/chat/projects/jobs/{job_id}/clarify", response_model=ChatRunResponse)
    def chat_project_job_clarify(job_id: str, request: ProjectJobClarificationRequest) -> ChatRunResponse:
        job, access = _validated_project_job(job_id, conversation_id=request.conversation_id)
        try:
            updated = answer_clarification(job, request.answer)
        except ProjectJobError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not repository.transition_project_job(updated, expected_statuses={"needs_clarification"}):
            raise HTTPException(status_code=409, detail="The clarification was already answered or replayed.")
        _sync_project_job_action(updated)
        run = build_job_chat_run(
            updated, message=request.answer,
            response="Clarification recorded. I refreshed the evidence-backed plan; no files were modified.",
            run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
        )
        repository.store_chat_run(run)
        audit_event(
            repository, conversation_id=updated["conversation_id"],
            folder_access_id=access["action_id"], job_id=job_id,
            operation="clarification_answered", status="completed",
            metadata={"answer_recorded": True},
        )
        audit_event(
            repository, conversation_id=updated["conversation_id"],
            folder_access_id=access["action_id"], job_id=job_id,
            operation="plan_creation", status="completed",
            metadata={"relative_paths": updated["relevant_paths"], "step_count": len(updated["implementation_plan"]["steps"])},
        )
        return run

    @application.post("/chat/projects/jobs/{job_id}/prepare", response_model=ChatRunResponse)
    def chat_project_job_prepare(job_id: str, request: ProjectJobActionRequest) -> ChatRunResponse:
        job, access = _validated_project_job(job_id, conversation_id=request.conversation_id)
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, operation="synthesis_requested", status="running",
            metadata={"analysis_id": job.get("analysis_id")},
        )
        def persist_started_attempt(attempt: dict) -> None:
            repository.store_project_synthesis_attempt(attempt)
            audit_event(
                repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                job_id=job_id, operation="model_generation_started", status="running",
                metadata={"attempt_id": attempt["attempt_id"], "provider": attempt.get("provider"),
                          "model": attempt.get("model"), "analysis_id": attempt.get("analysis_id")},
            )
        try:
            with synthesis_lock:
                current = repository.get_project_job(job_id)
                if current.get("status") not in {"planned", "blocked"}:
                    raise ProjectJobError("Patch preparation was already started or replayed.")
                job = current
                bundle = prepare_job_patch_bundle(
                    access["approved_root"], job, model_gateway=synthesis_gateway,
                    model_attempt_sink=persist_started_attempt,
                )
            changes = bundle["changes"]
            proposal = create_patch_proposal(
                root=access["approved_root"], conversation_id=job["conversation_id"],
                folder_access_id=access["action_id"], user_request=job["user_task"],
                changes=changes,
                files_inspected=[change["path"] for change in changes if change["operation"] != "create"],
                validation_plan=[str(item.get("purpose") or item.get("action")) for item in job.get("validation_plan") or []],
                job_id=job_id,
                analysis_context=bundle.get("analysis_context"),
            )
        except ModelSynthesisError as error:
            repository.store_project_synthesis_attempt(error.attempt)
            synthesis = _public_failed_synthesis(error)
            attempt_count = int(job.get("synthesis_attempt_count") or 0) + 1
            updated = {**job, "synthesis": synthesis, "synthesis_attempt_count": attempt_count,
                       "updated_at": datetime.now(timezone.utc).isoformat()}
            if error.code == "clarification_limit" or attempt_count >= int(job.get("max_synthesis_attempts") or 3):
                analysis = dict(job.get("analysis") or {})
                reasons = list(analysis.get("plan_only_reasons") or [])
                reason = "The bounded model synthesis or clarification attempt limit was reached."
                analysis.update({"plan_only": True, "plan_only_reasons": list(dict.fromkeys([*reasons, reason]))})
                updated["analysis"] = analysis
            if error.code == "needs_clarification":
                count = int(job.get("synthesis_clarification_count") or 0) + 1
                updated.update({
                    "status": "needs_clarification", "synthesis_clarification_count": count,
                    "clarification": {
                        "question": str((error.attempt.get("clarification") or {}).get("question") or str(error)),
                        "answer": None, "requested_at": datetime.now(timezone.utc).isoformat(), "answered_at": None,
                        "source": "model_assisted_synthesis",
                    },
                })
                repository.transition_project_job(updated, expected_statuses={str(job["status"])})
            else:
                repository.update_project_job(updated)
            _sync_project_job_action(updated)
            audit_event(
                repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                job_id=job_id, operation=_synthesis_audit_operation(error.code), status="blocked",
                metadata={"attempt_id": error.attempt["attempt_id"], "provider": error.attempt.get("provider"),
                          "model": error.attempt.get("model"), "reason": _controlled_project_error(error),
                          "analysis_id": job.get("analysis_id")},
            )
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        except (ProjectJobError, ProjectAnalysisError, ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            operation = "project_prevalidation_failed" if "validation" in str(error).lower() or "stale" in str(error).lower() else "synthesis_rejected"
            audit_event(
                repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                job_id=job_id, operation=operation, status="blocked",
                metadata={"reason": _controlled_project_error(error), "analysis_id": job.get("analysis_id")},
            )
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        repository.store_project_patch(proposal)
        if bundle.get("synthesis_attempt"):
            attempt = {**bundle["synthesis_attempt"], "status": "patch_proposed", "patch_id": proposal["patch_id"]}
            repository.store_project_synthesis_attempt(attempt)
        analysis = dict(job.get("analysis") or {})
        analysis["prevalidation"] = bundle.get("prevalidation") or {"status": "passed", "checks": [], "warnings": []}
        updated = {**job, "status": "patch_proposed", "analysis": analysis,
                   "synthesis": bundle.get("synthesis") or job.get("synthesis"),
                   "synthesis_attempt_count": int(job.get("synthesis_attempt_count") or 0) + (1 if bundle.get("synthesis_attempt") else 0),
                   "patch_ids": [*job.get("patch_ids", []), proposal["patch_id"]], "updated_at": datetime.now(timezone.utc).isoformat()}
        if not repository.transition_project_job(updated, expected_statuses={"planned", "blocked"}):
            raise HTTPException(status_code=409, detail="Patch preparation was already started or replayed.")
        _sync_project_job_action(updated)
        run = _project_patch_run(proposal)
        repository.store_chat_run(run)
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, patch_id=proposal["patch_id"], operation="patch_relationship",
            status="proposed", metadata={"relative_paths": proposal["file_set"]},
        )
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, patch_id=proposal["patch_id"], operation="pre_preview_validation",
            status="passed", metadata={"relative_paths": proposal["file_set"], "checks": analysis["prevalidation"].get("checks", [])},
        )
        if bundle.get("synthesis_attempt"):
            for operation, metadata in (
                ("model_generation_completed", {"response_hash": bundle["synthesis_attempt"].get("response_hash")}),
                ("model_response_normalized", {"relative_paths": proposal["file_set"]}),
                ("model_synthesis_confidence_evaluated", {"confidence": (bundle.get("synthesis") or {}).get("confidence", {}).get("level")}),
            ):
                audit_event(
                    repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                    job_id=job_id, patch_id=proposal["patch_id"], operation=operation,
                    status="completed", metadata={"attempt_id": bundle["synthesis_attempt"]["attempt_id"], **metadata},
                )
            audit_event(
                repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                job_id=job_id, patch_id=proposal["patch_id"], operation="model_synthesis_succeeded",
                status="completed", metadata={"attempt_id": bundle["synthesis_attempt"]["attempt_id"],
                                              "provider": bundle["synthesis_attempt"].get("provider"),
                                              "model": bundle["synthesis_attempt"].get("model"),
                                              "confidence": (bundle.get("synthesis") or {}).get("confidence", {}).get("level")},
            )
        return run

    @application.post("/chat/projects/jobs/{job_id}/validation", response_model=ChatRunResponse)
    def chat_project_job_validation(job_id: str, request: ProjectJobActionRequest) -> ChatRunResponse:
        job, access = _validated_project_job(job_id, conversation_id=request.conversation_id)
        if job.get("status") != "implementing":
            raise HTTPException(status_code=409, detail="Apply an approved job patch before proposing validation.")
        plans = list(job.get("validation_plan") or [])
        if not plans:
            raise HTTPException(status_code=409, detail="No allowlisted validation command was detected for this project job.")
        selected = plans[0]
        try:
            run = _plan_project_command_run(
                conversation_id=job["conversation_id"], access=access,
                message=str(selected.get("purpose") or "Validate the project job."),
                action=str(selected["action"]), target=selected.get("target"),
                expected_result=str(selected.get("expected_result") or "A bounded validation result."),
                timeout_seconds=120, job_id=job_id,
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        plan = run.action["technical_details"]["command_plan"] if run.action else {}
        repair = dict(job.get("repair") or {})
        if repair.get("status") == "applied_not_validated":
            repair = {**repair, "status": "validation_planned", "validation_rerun_status": "awaiting_approval"}
            try:
                cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                cycle = {**cycle, "status": "validation_planned", "validation_plan_id": plan.get("plan_id"),
                         "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_repair_cycle(cycle)
            except LookupError:
                pass
        updated = {
            **job, "status": "validating",
            "repair": repair,
            "command_plan_ids": [*job.get("command_plan_ids", []), plan.get("plan_id")],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not repository.transition_project_job(updated, expected_statuses={"implementing"}):
            raise HTTPException(status_code=409, detail="Validation was already proposed or replayed.")
        _sync_project_job_action(updated)
        repository.store_chat_run(run)
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, command_plan_id=plan.get("plan_id"), operation="validation_proposal",
            status="planned", metadata={"action": plan.get("action")},
        )
        if repair.get("status") == "validation_planned":
            audit_event(
                repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
                job_id=job_id, patch_id=repair.get("repair_patch_id"), command_plan_id=plan.get("plan_id"),
                operation="validation_rerun_planned", status="planned",
                metadata={"repair_cycle_id": repair.get("repair_cycle_id"), "cycle_number": repair.get("cycle_number")},
            )
        return run

    @application.post("/chat/projects/jobs/{job_id}/cancel")
    def chat_project_job_cancel(job_id: str, request: ProjectJobActionRequest) -> dict:
        job, access = _validated_project_job(job_id, conversation_id=request.conversation_id)
        if job.get("status") in {"completed", "cancelled"}:
            raise HTTPException(status_code=409, detail="The project job is already terminal.")
        updated = {**job, "status": "cancelled", "cancelled_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
        if not repository.transition_project_job(updated, expected_statuses={str(job["status"])}):
            raise HTTPException(status_code=409, detail="The project job changed concurrently or cancellation was replayed.")
        _sync_project_job_action(updated)
        audit_event(
            repository, conversation_id=job["conversation_id"], folder_access_id=access["action_id"],
            job_id=job_id, operation="job_cancelled", status="cancelled", metadata={},
        )
        return public_project_job(updated)

    @application.post("/chat/projects/patches/{patch_id}/approve", response_model=ChatRunResponse)
    def chat_project_patch_approve(patch_id: str, request: ProjectPatchApprovalRequest) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, patch_id)
        run = repository.get_chat_run(request.chat_run_id)
        proposal, snapshot = _get_project_patch(patch_id)
        job_relation = _job_for_patch(proposal)
        delivery_relation = _delivery_for_patch(proposal)
        access = _completed_project_access(run.conversation_id)
        try:
            verify_patch_approval(
                proposal, conversation_id=run.conversation_id,
                folder_access_id=access["action_id"], confirmation=request.confirmation,
            )
            validate_root_identity(access["approved_root"], str(proposal["root_fingerprint"]))
            from backend.app.folders.patches import validate_patch_fresh
            validate_patch_fresh(access["approved_root"], proposal)
        except (ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        if delivery_relation is not None:
            delivery, delivery_access = delivery_relation
            try:
                delivery_control.approve_patch(delivery, delivery_access["approved_root"], patch_id)
            except ProjectControlError as error:
                raise _control_http_error(error) from error
        proposal = {**proposal, "status": "approved", "approved_at": datetime.now(timezone.utc).isoformat()}
        if not repository.transition_project_patch(proposal, expected_status="proposed", snapshot=snapshot):
            raise HTTPException(status_code=409, detail="Patch approval was already used or changed concurrently.")
        repository.update_chat_run_action_for_id(
            request.chat_run_id, patch_id,
            {"status": "approved", "approval_required": False, "technical_details": {"project_patch": public_patch_proposal(proposal)}},
        )
        if job_relation is not None:
            job, _job_access = job_relation
            repair = dict(job.get("repair") or {})
            if proposal.get("patch_chain_context"):
                repair = {**repair, "status": "repair_approved"}
                try:
                    cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                    repository.update_project_repair_cycle({**cycle, "status": "repair_approved", "updated_at": datetime.now(timezone.utc).isoformat()})
                except LookupError:
                    pass
            updated_job = {**job, "status": "patch_approved", "repair": repair, "updated_at": datetime.now(timezone.utc).isoformat()}
            if not repository.transition_project_job(updated_job, expected_statuses={"patch_proposed"}):
                raise HTTPException(status_code=409, detail="The project job patch approval was already used or changed concurrently.")
            _sync_project_job_action(updated_job)
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="patch_approved", status="approved", metadata={"relative_paths": proposal["file_set"]})
        if proposal.get("patch_chain_context"):
            context = proposal["patch_chain_context"]
            audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], job_id=proposal.get("job_id"),
                        patch_id=patch_id, operation="repair_patch_approved", status="approved",
                        metadata={"repair_cycle_id": context.get("repair_cycle_id"), "cycle_number": context.get("cycle_number")})
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/projects/patches/{patch_id}/apply", response_model=ChatRunResponse)
    def chat_project_patch_apply(patch_id: str, request: ProjectPatchApplyRequest) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, patch_id)
        run = repository.get_chat_run(request.chat_run_id)
        proposal, _snapshot = _get_project_patch(patch_id)
        job_relation = _job_for_patch(proposal)
        delivery_relation = _delivery_for_patch(proposal)
        access = _completed_project_access(run.conversation_id)
        if delivery_relation is not None:
            delivery, delivery_access = delivery_relation
            try:
                canonical_run = project_control.get_project(str(delivery["delivery_job_id"]))
                current_manifest = build_project_state_manifest(
                    delivery_access["approved_root"],
                    workspace_id=canonical_run.workspace_id,
                )
                if current_manifest.manifest_hash != canonical_run.current_manifest_hash:
                    raise FileMutationError(
                        "stale_manifest",
                        "The repository changed after the patch authority was approved.",
                    )
                operations: list[FileOperationSpec] = []
                for change in proposal.get("changes") or []:
                    encoding = str(change.get("encoding") or "utf-8").lower().replace("_", "-")
                    if encoding not in {"utf-8", "utf8"}:
                        raise ValueError("Canonical mutations currently require UTF-8 project files.")
                    operation_name = str(change.get("operation") or "")
                    operation = {
                        "create": FileOperationKind.CREATE,
                        "modify": FileOperationKind.UPDATE,
                        "delete": FileOperationKind.DELETE,
                    }.get(operation_name)
                    if operation is None:
                        raise ValueError("The approved patch contains an unsupported operation.")
                    operations.append(FileOperationSpec(
                        relative_path=str(change.get("relative_path") or ""),
                        operation=operation,
                        preimage_sha256=(
                            None if operation == FileOperationKind.CREATE
                            else str(change.get("before_hash") or "")
                        ),
                        result_sha256=(
                            None if operation == FileOperationKind.DELETE
                            else str(change.get("after_hash") or "")
                        ),
                        new_content=(
                            None if operation == FileOperationKind.DELETE
                            else str(change.get("after_content") or "")
                        ),
                    ))
                attempt_key = f"legacy:patch-start:{patch_id}"
                attempt_id = (
                    "attempt-"
                    + content_hash([
                        canonical_run.project_run_id,
                        ExecutionAttemptType.PATCH.value,
                        attempt_key,
                    ])[:24]
                )
                expected_manifest_hash = calculate_expected_manifest_hash(
                    delivery_access["approved_root"],
                    workspace_id=canonical_run.workspace_id,
                    operations=operations,
                )
                mutation_spec = build_file_mutation_spec(
                    project_run_id=canonical_run.project_run_id,
                    execution_attempt_id=attempt_id,
                    mutation_kind=FileMutationKind.PATCH,
                    authority_id=patch_id,
                    repository_root=canonical_run.repository_root,
                    repository_root_fingerprint=canonical_run.repository_root_fingerprint,
                    workspace_id=canonical_run.workspace_id,
                    plan_revision_id=str(canonical_run.current_plan_revision_id),
                    scope_revision_id=str(canonical_run.current_scope_revision_id),
                    manifest_hash=str(canonical_run.current_manifest_hash),
                    expected_result_manifest_hash=expected_manifest_hash,
                    approved_paths=tuple(str(path) for path in proposal.get("file_set") or ()),
                    operations=tuple(operations),
                )
                canonical_read = delivery_control.begin_patch_application(
                    delivery,
                    delivery_access["approved_root"],
                    patch_id,
                    worker_dispatch={
                        "payload": {"file_mutation": mutation_spec.model_dump(mode="json")},
                        "idempotency_key": f"dispatch:{mutation_spec.file_mutation_id}",
                    },
                )
            except (ProjectControlError, FileMutationError, ValueError) as error:
                if isinstance(error, ProjectControlError):
                    raise _control_http_error(error) from error
                raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
            claimed = {**proposal, "status": "applying"}
            if not repository.transition_project_patch(claimed, expected_status="approved"):
                raise HTTPException(status_code=409, detail="Patch application was already started or completed.")
            dispatches = project_control.list_execution_dispatches(canonical_run.project_run_id)
            active_dispatch = dispatches[-1] if dispatches else None
            repository.update_chat_run_action_for_id(
                request.chat_run_id,
                patch_id,
                {
                    "status": "queued",
                    "approval_required": False,
                    "error": None,
                    "result_summary": "The exact approved patch is queued for the separate project worker.",
                    "technical_details": {
                        "project_patch": public_patch_proposal(claimed),
                        "canonical_project_run_id": canonical_read.project_run_id,
                        "execution_attempt_id": canonical_read.active_execution_attempt_id,
                        "execution_dispatch_id": (
                            active_dispatch.execution_dispatch_id if active_dispatch else None
                        ),
                        "worker_request_id": (
                            active_dispatch.worker_request_id if active_dispatch else None
                        ),
                        "execution_status": (
                            active_dispatch.status.value if active_dispatch else "pending"
                        ),
                    },
                },
            )
            audit_event(
                repository,
                conversation_id=run.conversation_id,
                folder_access_id=access["action_id"],
                patch_id=patch_id,
                operation="patch_queued",
                status="queued",
                metadata={
                    "execution_attempt_id": canonical_read.active_execution_attempt_id,
                    "execution_dispatch_id": (
                        active_dispatch.execution_dispatch_id if active_dispatch else None
                    ),
                },
            )
            return repository.get_chat_run(request.chat_run_id)
        raise HTTPException(
            status_code=409,
            detail={
                "schema_version": "astra.project-control.error.v1",
                "code": "historical_record_read_only",
                "message": "This historical project patch is read-only and has no canonical isolated execution binding.",
            },
        )
        claimed = {**proposal, "status": "applying"}
        if not repository.transition_project_patch(claimed, expected_status="approved"):
            raise HTTPException(status_code=409, detail="Patch application was already started or completed.")
        try:
            updated, snapshot = apply_project_patch(access["approved_root"], proposal)
        except (ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            failed = {**proposal, "status": "stale" if "stale" in str(error).lower() else "failed"}
            repository.update_project_patch(failed)
            repository.update_chat_run_action_for_id(request.chat_run_id, patch_id, {"status": failed["status"], "error": _controlled_project_error(error), "technical_details": {"project_patch": public_patch_proposal(failed)}})
            audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="patch_failed", status=failed["status"], metadata={"reason": _controlled_project_error(error)})
            if proposal.get("patch_chain_context"):
                context = proposal["patch_chain_context"]
                audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], job_id=proposal.get("job_id"),
                            patch_id=patch_id, operation="repair_proposal_became_stale" if failed["status"] == "stale" else "repair_preview_rejected",
                            status=failed["status"], metadata={"repair_cycle_id": context.get("repair_cycle_id"), "reason": _controlled_project_error(error)})
            if job_relation is not None:
                job, _job_access = job_relation
                blocked = {**job, "status": "blocked", "revision_count": int(job.get("revision_count") or 0) + 1, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(blocked)
                _sync_project_job_action(blocked)
                audit_event(
                    repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"],
                    job_id=job["job_id"], patch_id=patch_id, operation="job_blocked", status="blocked",
                    metadata={"reason": "patch_application_failed", "revision_count": blocked["revision_count"]},
                )
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        repository.update_project_patch(updated, snapshot)
        summary = _patch_application_summary(updated)
        repository.update_chat_run_action_for_id(
            request.chat_run_id, patch_id,
            {"status": "completed", "result_summary": summary, "error": None, "technical_details": {"project_patch": public_patch_proposal(updated), "rollback_available": True, "tests_run": False}},
        )
        if job_relation is not None:
            job, _job_access = job_relation
            repair = dict(job.get("repair") or {})
            if proposal.get("patch_chain_context"):
                repair = {**repair, "status": "applied_not_validated", "validation_rerun_status": "not_planned", "rollback_available": True}
                try:
                    cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                    repository.update_project_repair_cycle({**cycle, "status": "repair_applied", "updated_at": datetime.now(timezone.utc).isoformat()})
                except LookupError:
                    pass
            implementing = {**job, "status": "implementing", "repair": repair, "updated_at": datetime.now(timezone.utc).isoformat()}
            if not repository.transition_project_job(implementing, expected_statuses={"patch_approved"}):
                raise HTTPException(status_code=409, detail="The project job changed before patch application completed.")
            _sync_project_job_action(implementing)
        if delivery_relation is not None:
            delivery, _delivery_access = delivery_relation
            try:
                current_manifest = build_project_state_manifest(
                    access["approved_root"], workspace_id=delivery["folder_access_id"],
                )
                delivery_updated = record_delivery_patch_applied(
                    delivery, patch_id=patch_id,
                    current_state_hash=current_manifest.manifest_hash,
                )
                delivery_updated["project_state_manifest"] = current_manifest.model_dump(mode="json")
            except ProjectDeliveryError as error:
                raise _delivery_http_error(error) from error
            delivery_stored = _save_delivery_transition(
                delivery, delivery_updated, "patch_application", "applied",
                {"patch_id": patch_id, "work_unit_id": proposal.get("work_unit_id")},
            )
            _sync_delivery_action(delivery_stored)
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="patch_applied", status="applied", metadata={"relative_paths": updated["file_set"], "additions": updated["additions"], "deletions": updated["deletions"]})
        if proposal.get("patch_chain_context"):
            context = proposal["patch_chain_context"]
            audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], job_id=proposal.get("job_id"),
                        patch_id=patch_id, operation="repair_patch_applied", status="applied",
                        metadata={"repair_cycle_id": context.get("repair_cycle_id"), "cycle_number": context.get("cycle_number"), "validation_rerun": "not_started"})
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/projects/patches/{patch_id}/reject", response_model=ChatRunResponse)
    def chat_project_patch_reject(patch_id: str, request: ProjectPatchApplyRequest) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, patch_id)
        run = repository.get_chat_run(request.chat_run_id)
        access = _completed_project_access(run.conversation_id)
        proposal, snapshot = _get_project_patch(patch_id)
        job_relation = _job_for_patch(proposal)
        if proposal.get("status") != "proposed" or proposal.get("conversation_id") != run.conversation_id:
            raise HTTPException(status_code=409, detail="Only this conversation's pending patch can be rejected.")
        rejected = {**proposal, "status": "rejected"}
        if not repository.transition_project_patch(rejected, expected_status="proposed", snapshot=snapshot):
            raise HTTPException(status_code=409, detail="Patch was already approved, rejected, or changed concurrently.")
        repository.update_chat_run_action_for_id(request.chat_run_id, patch_id, {"status": "cancelled", "approval_required": False, "result_summary": "Patch rejected. No files were changed.", "technical_details": {"project_patch": public_patch_proposal(rejected)}})
        if job_relation is not None:
            job, _job_access = job_relation
            planned = {**job, "status": "planned", "updated_at": datetime.now(timezone.utc).isoformat()}
            repository.update_project_job(planned)
            _sync_project_job_action(planned)
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="patch_rejected", status="rejected", metadata={"relative_paths": rejected["file_set"]})
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/projects/rollback/request", response_model=ChatRunResponse)
    def chat_project_rollback_request(request: ProjectRollbackRequest) -> ChatRunResponse:
        access = _completed_project_access(request.conversation_id)
        active_delivery = repository.latest_active_project_delivery_job(
            request.conversation_id
        )
        if active_delivery is not None:
            project_projection.rebuild_project(
                str(active_delivery["delivery_job_id"])
            )
        try:
            proposal, _snapshot = repository.latest_applied_project_patch(request.conversation_id)
        except LookupError as error:
            projected_delivery = (
                repository.get_project_delivery_job(
                    str(active_delivery["delivery_job_id"])
                )
                if active_delivery is not None else {}
            )
            applied_reference = next((
                item
                for item in reversed(projected_delivery.get("patch_references") or [])
                if item.get("status") == "applied" and item.get("patch_id")
            ), None)
            if applied_reference is None:
                raise HTTPException(status_code=404, detail=str(error)) from error
            proposal, _snapshot = _get_project_patch(
                str(applied_reference["patch_id"])
            )
        if proposal.get("folder_access_id") != access["action_id"]:
            raise HTTPException(status_code=409, detail="The patch belongs to a different folder access.")
        run = _project_rollback_run(proposal, request.user_message)
        repository.store_chat_run(run)
        delivery_relation = _delivery_for_patch(proposal)
        if delivery_relation is not None:
            delivery, delivery_access = delivery_relation
            try:
                delivery_control.record_rollback_preview(
                    delivery,
                    delivery_access["approved_root"],
                    f"rollback:{proposal['patch_id']}",
                )
            except ProjectControlError as error:
                raise _control_http_error(error) from error
        audit_event(repository, conversation_id=request.conversation_id, folder_access_id=access["action_id"], patch_id=proposal["patch_id"], operation="rollback_proposed", status="awaiting_approval", metadata={"relative_paths": proposal["file_set"]})
        return run

    @application.post("/chat/projects/rollback/{patch_id}/approve", response_model=ChatRunResponse)
    def chat_project_rollback_approve(patch_id: str, request: ProjectPatchApprovalRequest) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, f"rollback:{patch_id}")
        run = repository.get_chat_run(request.chat_run_id)
        access = _completed_project_access(run.conversation_id)
        proposal, snapshot = _get_project_patch(patch_id)
        if request.confirmation != f"APPROVE ROLLBACK {patch_id}":
            raise HTTPException(status_code=400, detail=f"Explicit confirmation must exactly match: APPROVE ROLLBACK {patch_id}")
        if proposal.get("folder_access_id") != access["action_id"]:
            raise HTTPException(status_code=409, detail="Rollback is unavailable or belongs to another folder access.")
        delivery_relation = _delivery_for_patch(proposal)
        if proposal.get("status") != "applied" and not (
            delivery_relation is not None and proposal.get("status") == "applying"
        ):
            raise HTTPException(status_code=409, detail="Rollback is unavailable or belongs to another folder access.")
        if delivery_relation is not None:
            delivery, delivery_access = delivery_relation
            canonical_run = project_control.get_project(str(delivery["delivery_job_id"]))
            completed_patch = next(
                (
                    attempt
                    for attempt in reversed(project_control.list_attempts(canonical_run.project_run_id))
                    if attempt.attempt_type == ExecutionAttemptType.PATCH
                    and attempt.status.value == "completed"
                    and str(attempt.authority.get("patch_id") or "") == patch_id
                ),
                None,
            )
            file_mutation_id = str(
                (completed_patch.result_reference or {}).get("file_mutation_id")
                if completed_patch is not None
                else ""
            )
            if not file_mutation_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "rollback_snapshot_unavailable",
                        "message": "The canonical patch has no durable rollback snapshot.",
                    },
                )
            rollback_id = f"rollback:{patch_id}"
            try:
                operations = project_mutation_engine.build_rollback_operations(
                    file_mutation_id
                )
                attempt_id = (
                    "attempt-"
                    + content_hash([
                        canonical_run.project_run_id,
                        ExecutionAttemptType.ROLLBACK.value,
                        f"legacy:rollback-start:{rollback_id}",
                    ])[:24]
                )
                expected_manifest_hash = calculate_expected_manifest_hash(
                    access["approved_root"],
                    workspace_id=canonical_run.workspace_id,
                    operations=operations,
                )
                mutation_spec = build_file_mutation_spec(
                    project_run_id=canonical_run.project_run_id,
                    execution_attempt_id=attempt_id,
                    mutation_kind=FileMutationKind.ROLLBACK,
                    authority_id=rollback_id,
                    repository_root=canonical_run.repository_root,
                    repository_root_fingerprint=canonical_run.repository_root_fingerprint,
                    workspace_id=canonical_run.workspace_id,
                    plan_revision_id=str(canonical_run.current_plan_revision_id),
                    scope_revision_id=str(canonical_run.current_scope_revision_id),
                    manifest_hash=str(canonical_run.current_manifest_hash),
                    expected_result_manifest_hash=expected_manifest_hash,
                    approved_paths=tuple(item.relative_path for item in operations),
                    operations=operations,
                )
                delivery_control.approve_rollback(
                    delivery,
                    delivery_access["approved_root"],
                    rollback_id,
                    mutation_spec_hash=mutation_spec.spec_hash,
                )
                canonical_read = delivery_control.begin_rollback(
                    delivery,
                    delivery_access["approved_root"],
                    rollback_id,
                    mutation_spec_hash=mutation_spec.spec_hash,
                    worker_dispatch={
                        "payload": {"file_mutation": mutation_spec.model_dump(mode="json")},
                        "idempotency_key": f"dispatch:{mutation_spec.file_mutation_id}",
                    },
                )
            except (ProjectControlError, FileMutationError, ValueError) as error:
                if isinstance(error, ProjectControlError):
                    raise _control_http_error(error) from error
                raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
            approved = {**proposal, "status": "rollback_approved"}
            if not repository.transition_project_patch(
                approved, expected_status=str(proposal["status"]), snapshot=snapshot
            ):
                raise HTTPException(status_code=409, detail="Rollback was already started or completed.")
            repository.update_chat_run_action_for_id(
                request.chat_run_id,
                rollback_id,
                {
                    "status": "queued",
                    "approval_required": False,
                    "result_summary": "The exact approved rollback is queued for the separate project worker.",
                    "technical_details": {
                        "project_rollback": {
                            "patch_id": patch_id,
                            "rollback_id": rollback_id,
                            "relative_paths": proposal["file_set"],
                            "status": "queued",
                            "execution_attempt_id": canonical_read.active_execution_attempt_id,
                            "execution_dispatch_id": canonical_read.execution_dispatch_id,
                        }
                    },
                },
            )
            audit_event(
                repository,
                conversation_id=run.conversation_id,
                folder_access_id=access["action_id"],
                patch_id=patch_id,
                operation="rollback_queued",
                status="queued",
                metadata={
                    "rollback_id": rollback_id,
                    "execution_attempt_id": canonical_read.active_execution_attempt_id,
                    "execution_dispatch_id": canonical_read.execution_dispatch_id,
                },
            )
            return repository.get_chat_run(request.chat_run_id)
        raise HTTPException(
            status_code=409,
            detail={
                "schema_version": "astra.project-control.error.v1",
                "code": "historical_record_read_only",
                "message": "This historical rollback is read-only and cannot mutate project files on the host.",
            },
        )
        approved = {**proposal, "status": "rollback_approved"}
        if not repository.transition_project_patch(approved, expected_status="applied", snapshot=snapshot):
            raise HTTPException(status_code=409, detail="Rollback was already started or completed.")
        try:
            rolled_back = rollback_project_patch(access["approved_root"], approved, snapshot)
        except (ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
            repository.update_project_patch(proposal, snapshot)
            audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="rollback_refused", status="conflict", metadata={"reason": _controlled_project_error(error)})
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        repository.update_project_patch(rolled_back, snapshot)
        summary = f"Rolled back patch {patch_id[:8]} and restored {len(snapshot)} file(s)."
        repository.update_chat_run_action_for_id(request.chat_run_id, f"rollback:{patch_id}", {"status": "completed", "approval_required": False, "result_summary": summary, "technical_details": {"project_rollback": {"patch_id": patch_id, "relative_paths": rolled_back["file_set"], "status": "rolled_back"}}})
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="rollback_completed", status="rolled_back", metadata={"relative_paths": rolled_back["file_set"]})
        delivery_relation = _delivery_for_patch(proposal)
        if delivery_relation is not None:
            delivery, _delivery_access = delivery_relation
            try:
                restored_manifest = build_project_state_manifest(
                    access["approved_root"], workspace_id=delivery["folder_access_id"],
                )
                delivery_updated = record_delivery_rollback(
                    delivery, patch_id=patch_id,
                    restored_state_hash=restored_manifest.manifest_hash,
                )
                delivery_updated["project_state_manifest"] = restored_manifest.model_dump(mode="json")
            except ProjectDeliveryError as error:
                raise _delivery_http_error(error) from error
            delivery_stored = _save_delivery_transition(
                delivery, delivery_updated, "rollback_execution", "rolled_back", {"patch_id": patch_id},
            )
            _sync_delivery_action(delivery_stored)
        if proposal.get("patch_chain_context") and proposal.get("job_id"):
            try:
                job = repository.get_project_job(str(proposal["job_id"]))
                repair = {**dict(job.get("repair") or {}), "status": "rolled_back", "rollback_available": False,
                          "validation_rerun_status": "invalidated_by_rollback"}
                job = {**job, "status": "implementing", "repair": repair,
                       "completion_summary": None, "completed_at": None,
                       "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(job)
                _sync_project_job_action(job)
                cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                repository.update_project_repair_cycle({**cycle, "status": "rolled_back", "updated_at": datetime.now(timezone.utc).isoformat()})
                audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], job_id=job["job_id"],
                            patch_id=patch_id, operation="repair_rollback_completed", status="rolled_back",
                            metadata={"repair_cycle_id": repair.get("repair_cycle_id"), "cycle_number": repair.get("cycle_number")})
            except LookupError:
                pass
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/projects/rollback/{patch_id}/reject", response_model=ChatRunResponse)
    def chat_project_rollback_reject(patch_id: str, request: ProjectPatchApplyRequest) -> ChatRunResponse:
        _require_folder_action_association(request.chat_run_id, f"rollback:{patch_id}")
        run = repository.get_chat_run(request.chat_run_id)
        access = _completed_project_access(run.conversation_id)
        repository.update_chat_run_action_for_id(request.chat_run_id, f"rollback:{patch_id}", {"status": "cancelled", "approval_required": False, "result_summary": "Rollback cancelled. No files were changed."})
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=patch_id, operation="rollback_refused", status="cancelled", metadata={"reason": "user_cancelled"})
        return repository.get_chat_run(request.chat_run_id)

    @application.post("/chat/projects/commands/propose", response_model=ChatRunResponse)
    def chat_project_command_propose(request: ProjectCommandProposalRequest) -> ChatRunResponse:
        access = _completed_project_access(request.conversation_id)
        try:
            run = _plan_project_command_run(
                conversation_id=request.conversation_id, access=access,
                message=request.purpose, action=request.action, target=request.target,
                expected_result=request.expected_result, timeout_seconds=request.timeout_seconds,
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        repository.store_chat_run(run)
        plan = run.action["technical_details"]["command_plan"] if run.action else {}
        audit_event(repository, conversation_id=request.conversation_id, folder_access_id=access["action_id"], operation="command_planned", status="planned", metadata={"plan_id": plan.get("plan_id"), "action": plan.get("action")})
        return run

    @application.post("/chat/projects/commands/{plan_id}/approve")
    def chat_project_command_approve(plan_id: str, request: AssignmentCommandApprovalRequest) -> dict:
        _require_chat_command_association(request.chat_run_id, plan_id)
        run = repository.get_chat_run(request.chat_run_id or "")
        access = _completed_project_access(run.conversation_id)
        job_id = _validate_job_command(plan_id, run, access)
        try:
            plan, token = approve_assignment_command(
                assignment_command_store, plan_id,
                assignment_id=request.assignment_id, workspace=access["approved_root"],
                project_root=access["approved_root"], confirmation=request.confirmation,
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        if job_id:
            bridge = repository.get_project_job(job_id)
            delivery_id = bridge.get("delivery_job_id")
            if delivery_id:
                delivery, delivery_access = _validated_project_delivery(str(delivery_id), conversation_id=run.conversation_id)
                try:
                    execution_spec = _canonical_execution_spec(plan)
                    delivery_control.approve_command(
                        delivery,
                        delivery_access["approved_root"],
                        plan_id,
                        execution_hash=execution_spec.execution_hash,
                    )
                except (ProjectControlError, CommandExecutionError, ValueError) as error:
                    if isinstance(error, ProjectControlError):
                        raise _control_http_error(error) from error
                    raise HTTPException(
                        status_code=400, detail=_controlled_project_error(error)
                    ) from error
        repository.update_chat_run_action_for_plan(request.chat_run_id or "", plan_id, {"status": "approved", "technical_details": {"command_plan": plan}})
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], job_id=job_id, command_plan_id=plan_id, operation="command_approved", status="approved", metadata={"action": plan.get("action")})
        return {"plan": plan, "approval_token": token}

    @application.post("/chat/projects/commands/{plan_id}/execute")
    def chat_project_command_execute(plan_id: str, request: AssignmentCommandExecuteRequest) -> dict:
        _require_chat_command_association(request.chat_run_id, plan_id)
        run = repository.get_chat_run(request.chat_run_id or "")
        access = _completed_project_access(run.conversation_id)
        preflight_job_id = _validate_job_command(plan_id, run, access)
        if preflight_job_id:
            bridge = repository.get_project_job(preflight_job_id)
            delivery_id = bridge.get("delivery_job_id")
            if delivery_id:
                delivery, delivery_access = _validated_project_delivery(str(delivery_id), conversation_id=run.conversation_id)
                try:
                    command = validate_assignment_command_execution(
                        assignment_command_store,
                        access["approved_root"],
                        plan_id,
                        assignment_id=request.assignment_id,
                        workspace=access["approved_root"],
                        approval_token=request.approval_token,
                    )
                    execution_spec = _canonical_execution_spec(command)
                    canonical = delivery_control.begin_command_execution(
                        delivery,
                        delivery_access["approved_root"],
                        plan_id,
                        execution_hash=execution_spec.execution_hash,
                        worker_dispatch=_canonical_command_dispatch(command, execution_spec),
                    )
                except ProjectControlError as error:
                    raise _control_http_error(error) from error
                except (CommandExecutionError, ValueError, FileNotFoundError) as error:
                    raise HTTPException(
                        status_code=400, detail=_controlled_project_error(error)
                    ) from error
                queued = {
                    **command,
                    "status": "queued",
                    "display_state": "queued",
                    "canonical_project": canonical.model_dump(mode="json"),
                }
                repository.update_chat_run_action_for_plan(
                    request.chat_run_id or "",
                    plan_id,
                    {
                        "status": "queued",
                        "result_summary": "The exact approved command is queued for isolated execution.",
                        "technical_details": {"command_plan": queued},
                    },
                )
                audit_event(
                    repository,
                    conversation_id=run.conversation_id,
                    folder_access_id=access["action_id"],
                    job_id=preflight_job_id,
                    command_plan_id=plan_id,
                    operation="command_queued",
                    status="queued",
                    metadata={
                        "action": command.get("action"),
                        "execution_attempt_id": canonical.active_execution_attempt_id,
                        "execution_dispatch_id": canonical.execution_dispatch_id,
                    },
                )
                return queued
        if preflight_job_id is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "This connected project command has no canonical isolated execution "
                    "binding. No project code was executed on the host. Create or resume "
                    "the canonical project delivery and retry when the Docker worker is available."
                ),
            )
        raise HTTPException(
            status_code=409,
            detail={
                "schema_version": "astra.project-control.error.v1",
                "code": "historical_record_read_only",
                "message": "This historical project command has no canonical isolated execution binding and cannot run on the host.",
            },
        )
        try:
            result = execute_assignment_command(
                assignment_command_store, access["approved_root"], plan_id,
                assignment_id=request.assignment_id, workspace=access["approved_root"],
                approval_token=request.approval_token,
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        summary, error_tail = _chat_command_result_presentation(result)
        succeeded = result.get("exit_code") == 0 and result.get("display_state") == "completed"
        repository.update_chat_run_action_for_plan(request.chat_run_id or "", plan_id, {"status": "completed" if succeeded else "failed", "result_summary": summary, "error": error_tail, "technical_details": {"command_plan": result}})
        job_id = _job_id_from_command(result)
        if job_id != preflight_job_id:
            raise HTTPException(status_code=409, detail="The command-to-job association changed before execution.")
        if job_id:
            job, _job_access = _validated_project_job(job_id, conversation_id=run.conversation_id)
            if plan_id not in job.get("command_plan_ids", []):
                raise HTTPException(status_code=409, detail="This command is not associated with the project job.")
            interpretation = interpret_validation_result(result, job.get("analysis_index"))
            validation_results = [*job.get("validation_results", []), interpretation]
            if succeeded:
                patches = repository.list_project_patches_for_job(job_id)
                repair = dict(job.get("repair") or {})
                if repair.get("repair_cycle_id") and repair.get("status") in {"validation_planned", "applied_not_validated"}:
                    try:
                        cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                        cycle = {**cycle, "status": "validated", "validation_plan_id": plan_id,
                                 "validation_execution_id": result.get("execution_id"), "updated_at": datetime.now(timezone.utc).isoformat()}
                        repository.update_project_repair_cycle(cycle)
                        repair = {**repair, "status": "validated", "validation_rerun_status": "passed"}
                    except LookupError:
                        pass
                completed_job = {
                    **job, "status": "completed", "validation_results": validation_results, "repair": repair,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                completed_job["completion_summary"] = build_completion_summary(completed_job, patches)
                if not repository.transition_project_job(completed_job, expected_statuses={"validating"}):
                    raise HTTPException(status_code=409, detail="The project job validation result was already recorded or replayed.")
                _sync_project_job_action(completed_job)
                audit_event(
                    repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"],
                    job_id=job_id, command_plan_id=plan_id, operation="job_completed", status="completed",
                    metadata={"relative_paths": completed_job["completion_summary"]["files_changed"], "validation_status": "passed"},
                )
                if repair.get("status") == "validated":
                    audit_event(
                        repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"],
                        job_id=job_id, command_plan_id=plan_id, patch_id=repair.get("repair_patch_id"),
                        operation="validation_rerun_passed", status="passed",
                        metadata={"repair_cycle_id": repair.get("repair_cycle_id"), "cycle_number": repair.get("cycle_number"),
                                  "command_execution_id": result.get("execution_id")},
                    )
            else:
                prior_repair = dict(job.get("repair") or {})
                if prior_repair.get("repair_cycle_id") and prior_repair.get("status") in {"validation_planned", "applied_not_validated"}:
                    try:
                        prior_cycle = repository.get_project_repair_cycle(str(prior_repair["repair_cycle_id"]))
                        prior_cycle = {**prior_cycle, "status": "validation_failed", "validation_plan_id": plan_id,
                                       "validation_execution_id": result.get("execution_id"), "updated_at": datetime.now(timezone.utc).isoformat()}
                        repository.update_project_repair_cycle(prior_cycle)
                    except LookupError:
                        pass
                    audit_event(
                        repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"],
                        job_id=job_id, command_plan_id=plan_id, patch_id=prior_repair.get("repair_patch_id"),
                        operation="validation_rerun_failed", status="failed",
                        metadata={"repair_cycle_id": prior_repair.get("repair_cycle_id"),
                                  "cycle_number": prior_repair.get("cycle_number"),
                                  "command_execution_id": result.get("execution_id")},
                    )
                evidence, cycle = _capture_project_failure(job, access, result)
                limit_reached = (
                    int(cycle.get("cycle_number") or 0) > int(job.get("max_repair_cycles") or MAX_REPAIR_CYCLES)
                    or int(job.get("repair_failure_count") or 0) + 1 > int(job.get("max_repair_failures") or MAX_REPAIR_FAILURES)
                )
                repair = {
                    "status": "limit_reached" if limit_reached else "offered",
                    "repair_chain_id": cycle["repair_chain_id"], "repair_cycle_id": cycle["repair_cycle_id"],
                    "cycle_number": cycle["cycle_number"], "failure_evidence_id": evidence["evidence_id"],
                    "diagnosis_id": None, "parent_patch_id": cycle["parent_patch_id"], "repair_patch_id": None,
                    "command_execution_id": evidence["command_execution_id"], "diagnosis_strategy": None,
                    "provider": None, "model": None, "confidence": None, "root_causes": [],
                    "affected_files": evidence.get("referenced_files") or interpretation["likely_affected_paths"],
                    "affected_symbols": interpretation["likely_affected_symbols"], "assumptions": [],
                    "warnings": evidence.get("uncertainty_codes") or [], "clarification": None,
                    "failed_command_summary": interpretation["summary"],
                    "failure_output_truncated": evidence.get("output_truncated", False),
                    "failure_redaction_count": len(evidence.get("redaction_summary") or []),
                    "validation_rerun_status": "failed" if prior_repair.get("repair_cycle_id") else "not_planned",
                    "rollback_available": False,
                }
                blocked_job = {
                    **job, "status": "blocked", "validation_results": validation_results,
                    "revision_count": int(job.get("revision_count") or 0) + 1,
                    "repair": repair,
                    "repair_cycle_count": max(int(job.get("repair_cycle_count") or 0), int(cycle["cycle_number"])),
                    "repair_failure_count": int(job.get("repair_failure_count") or 0) + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                if not repository.transition_project_job(blocked_job, expected_statuses={"validating"}):
                    raise HTTPException(status_code=409, detail="The project job validation result was already recorded or replayed.")
                _sync_project_job_action(blocked_job)
                audit_event(
                    repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"],
                    job_id=job_id, command_plan_id=plan_id, operation="job_blocked", status="blocked",
                    metadata={"validation_status": "failed", "relative_paths": interpretation["likely_affected_paths"], "revision_count": blocked_job["revision_count"]},
                )
                for operation, status, metadata in (
                    ("approved_command_failed", "failed", {"command_execution_id": evidence["command_execution_id"], "exit_code": evidence.get("exit_code")}),
                    ("failure_output_captured", "completed", {"failure_evidence_id": evidence["evidence_id"], "diagnostic_count": len(evidence.get("diagnostics") or [])}),
                    ("failure_evidence_redacted", "completed", {"failure_evidence_id": evidence["evidence_id"], "redaction_count": len(evidence.get("redaction_summary") or [])}),
                    ("failure_evidence_truncated", "completed" if evidence.get("output_truncated") else "not_needed", {"failure_evidence_id": evidence["evidence_id"], "truncated": bool(evidence.get("output_truncated"))}),
                    ("diagnosis_offered", "blocked" if not limit_reached else "limit_reached", {"failure_evidence_id": evidence["evidence_id"], "repair_cycle_id": cycle["repair_cycle_id"], "cycle_number": cycle["cycle_number"]}),
                ):
                    audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"],
                                job_id=job_id, patch_id=cycle["parent_patch_id"], command_plan_id=plan_id,
                                operation=operation, status=status, metadata=metadata)
        if job_id:
            bridge = repository.get_project_job(job_id)
            delivery_id = bridge.get("delivery_job_id")
            if delivery_id:
                delivery, _delivery_access = _validated_project_delivery(str(delivery_id), conversation_id=run.conversation_id)
                references = [dict(item) for item in delivery.get("command_references") or []]
                reference = next((item for item in references if item.get("plan_id") == plan_id), None)
                if reference is None:
                    reference = next((item for item in reversed(references) if item.get("status") == "failed"), None)
                    if reference is not None:
                        reference = {**reference, "plan_id": plan_id, "status": "running", "repair_rerun": True}
                        references.append(reference)
                if reference is not None:
                    reference["status"] = "passed" if succeeded else "failed"
                    reference["execution_id"] = result.get("execution_id")
                    reference["finished_at"] = result.get("finished_at")
                    delivery_with_command = json.loads(json.dumps(delivery))
                    delivery_with_command["command_references"] = references
                    if succeeded:
                        delivery_with_command["status"] = DeliveryStatus.PATCH_APPLIED.value
                    budgets = dict(delivery_with_command.get("budgets") or {})
                    budgets["command_executions"] = int(budgets.get("command_executions", 0)) + 1
                    delivery_with_command["budgets"] = budgets
                    _delivery_audit(delivery, "verifier_start", "started", {
                        "criterion_id": reference["criterion_id"], "verifier_type": reference["method"],
                    })
                    try:
                        verifier = run_deterministic_verifier(
                            delivery_with_command, root=_delivery_access["approved_root"],
                            criterion_id=str(reference["criterion_id"]), command_result=result,
                        )
                        delivery_updated = record_delivery_verification(
                            delivery_with_command,
                            work_unit_id=str(reference["work_unit_id"]),
                            criterion_id=str(reference["criterion_id"]),
                            state=VerificationState.SATISFIED if verifier.outcome == VerifierOutcome.PASSED else VerificationState.FAILED,
                            method=VerificationMode(str(reference["method"])),
                            evidence_references=[str(result.get("execution_id") or plan_id)],
                            command_run_references=[str(result.get("execution_id") or plan_id)],
                            failure_explanation=None if succeeded else summary,
                            verifier_result=verifier,
                        )
                    except (ProjectDeliveryError, ProjectVerifierError, ProjectManifestError) as error:
                        operation = "stale_verifier_rejected" if getattr(error, "code", "") == "stale_verifier_result" else "verifier_completion"
                        _delivery_audit(delivery, operation, "rejected", {
                            "criterion_id": reference["criterion_id"], "error_code": getattr(error, "code", "verifier_error"),
                        })
                        raise _delivery_http_error(error) from error
                    if not succeeded:
                        latest_bridge = repository.get_project_job(job_id)
                        delivery_updated["status"] = DeliveryStatus.DIAGNOSING.value
                        delivery_updated["stage8"] = {
                            "project_job_id": job_id,
                            "repair": latest_bridge.get("repair"),
                            "verification_plan_id": plan_id,
                            "criterion_id": reference["criterion_id"],
                        }
                    delivery_updated["updated_at"] = datetime.now(timezone.utc).isoformat()
                    delivery_stored = _save_delivery_transition(
                        delivery, delivery_updated, "verifier_completion", "passed" if succeeded else "stage8_diagnosis",
                        {"plan_id": plan_id, "criterion_id": reference["criterion_id"], "exit_code": result.get("exit_code")},
                    )
                    _sync_delivery_action(delivery_stored)
        audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], job_id=job_id, command_plan_id=plan_id, operation="validation_interpretation" if job_id else ("command_executed" if succeeded else "command_failed"), status="completed" if succeeded else "failed", metadata={"action": result.get("action"), "exit_code": result.get("exit_code"), "duration": result.get("duration_seconds")})
        return result

    @application.post("/chat/projects/commands/{plan_id}/cancel")
    def chat_project_command_cancel(plan_id: str, request: AssignmentCommandAssociationRequest) -> dict:
        _require_chat_command_association(request.chat_run_id, plan_id)
        run = repository.get_chat_run(request.chat_run_id or "")
        access = _completed_project_access(run.conversation_id)
        preflight_job_id = _validate_job_command(plan_id, run, access)
        try:
            result = cancel_assignment_command(
                assignment_command_store, plan_id,
                assignment_id=request.assignment_id, workspace=access["approved_root"],
                project_root=access["approved_root"],
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        repository.update_chat_run_action_for_plan(request.chat_run_id or "", plan_id, {"status": "cancelled", "result_summary": "Validation command cancelled. Nothing was executed.", "technical_details": {"command_plan": result}})
        job_id = _job_id_from_command(result)
        if job_id != preflight_job_id:
            raise HTTPException(status_code=409, detail="The command-to-job association changed before cancellation.")
        if job_id:
            job, _job_access = _validated_project_job(job_id, conversation_id=run.conversation_id)
            if plan_id not in job.get("command_plan_ids", []):
                raise HTTPException(status_code=409, detail="This command is not associated with the project job.")
            implementing = {**job, "status": "implementing", "updated_at": datetime.now(timezone.utc).isoformat()}
            if repository.transition_project_job(implementing, expected_statuses={"validating"}):
                _sync_project_job_action(implementing)
        return result

    @application.post("/datasets/profile")
    def dataset_profile(request: DatasetProfileRequest) -> dict:
        try:
            profile = profile_csv_dataset(
                _resolve_dataset_path(request.path),
                sample_rows=request.sample_rows,
                row_count_override=request.row_count_override,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return profile.model_dump(mode="json")

    @application.post("/assignments/workspace/plan")
    def assignment_workspace_plan(request: AssignmentWorkspacePlanRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            root = _resolve_workspace_write_path(request.workspace_path)
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            plan = plan_assignment_workspace(
                brief,
                assignment_number=request.assignment_number,
                workspace_root=root,
                dataset_profile=dataset_profile,
                write_files=request.write_files,
                overwrite=request.overwrite,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return plan.model_dump(mode="json")

    @application.post("/assignments/workspace/generate")
    def assignment_workspace_generate(
        request: AssignmentWorkspaceGenerateRequest,
    ) -> dict:
        try:
            return _generate_assignment_workspace_from_payload(request)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/assignments/runbook/generate")
    def assignment_runbook_generate(request: AssignmentRunbookGenerateRequest) -> dict:
        try:
            root = _resolve_workspace_write_path(request.workspace_path)
            runbook = generate_assignment_runbook(request.assignment_number, workspace_root=root)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return runbook.model_dump(mode="json")

    @application.post("/assignments/report/export")
    def assignment_report_export(request: AssignmentReportExportRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            root = _resolve_workspace_write_path(request.workspace_path)
            evidence = build_evidence_checklist(brief)
            report = generate_report_draft(brief, evidence=evidence)
            runbook = generate_assignment_runbook(request.assignment_number, workspace_root=root)
            readiness = check_marking_readiness(brief, evidence)
            result = export_report_package(
                root,
                report_draft=report,
                evidence=evidence,
                runbook=runbook,
                marking_readiness=readiness,
                report_folder=request.report_folder,
                overwrite=request.overwrite,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/code/blueprint")
    def assignment_code_blueprint(request: AssignmentCodeBlueprintRequest) -> dict:
        try:
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            result = generate_code_blueprints(
                request.assignment_number,
                dataset_profile=dataset_profile,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/code/write")
    def assignment_code_write(request: AssignmentCodeWriteRequest) -> dict:
        try:
            root = _resolve_workspace_write_path(request.workspace_path)
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            blueprint_sets = _blueprint_sets_from_request(request, dataset_profile)
            result = write_code_blueprints(root, blueprint_sets, overwrite=request.overwrite)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/dataset/map")
    def assignment_dataset_map(request: AssignmentDatasetMapRequest) -> dict:
        try:
            if request.dataset_profile is not None:
                from backend.app.datasets.schemas import DatasetProfile

                profile = DatasetProfile.model_validate(request.dataset_profile)
            elif request.dataset_path:
                profile = profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
            else:
                profile = None
            result = map_dataset_columns(profile)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/manifest/build")
    def assignment_manifest_build(request: AssignmentManifestBuildRequest) -> dict:
        manifest = build_assignment_manifest(
            request.copilot_result,
            assignment_number=request.assignment_number,
            dataset_path=request.dataset_path,
            document_path=request.document_path,
        )
        return manifest.model_dump(mode="json")

    @application.post("/assignments/manifest/write")
    def assignment_manifest_write(request: AssignmentManifestWriteRequest) -> dict:
        try:
            root = _resolve_workspace_write_path(request.workspace_path)
            if request.manifest is not None:
                manifest = AssignmentProjectManifest.model_validate(request.manifest)
            elif request.copilot_result is not None:
                manifest = build_assignment_manifest(
                    request.copilot_result,
                    assignment_number=request.assignment_number,
                    dataset_path=request.dataset_path,
                    document_path=request.document_path,
                )
            else:
                raise ValueError("Either manifest or copilot_result is required.")
            result = write_assignment_manifest(root, manifest, overwrite=request.overwrite)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/analysis/plan")
    def assignment_analysis_plan(request: AssignmentAnalysisPlanRequest) -> dict:
        try:
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            result = generate_analysis_plan(
                request.assignment_number,
                dataset_profile=dataset_profile,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/dashboard/spec")
    def assignment_dashboard_spec(request: AssignmentDashboardSpecRequest) -> dict:
        try:
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            result = generate_dashboard_spec(
                request.assignment_number,
                dataset_profile=dataset_profile,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/assignments/final-readiness")
    def assignment_final_readiness(request: AssignmentFinalReadinessRequest) -> dict:
        try:
            brief = _assignment_brief_from_payload(request)
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            workspace_inspection = (
                inspect_workspace(_resolve_workspace_path(request.workspace_path))
                if request.workspace_path
                else None
            )
            result = build_final_readiness_report(
                brief,
                assignment_number=request.assignment_number,
                dataset_profile=dataset_profile,
                workspace_inspection=workspace_inspection,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    def _assignment_brief_from_request(request: AssignmentExtractRequest) -> AssignmentBrief:
        if request.path:
            parsed = parse_assignment_document(_resolve_assignment_path(request.path))
            return extract_assignment_brief(parsed)
        if request.text:
            title = request.title or "Assignment brief"
            from backend.app.assignments.schemas import ParsedAssignmentDocument

            parsed = ParsedAssignmentDocument(
                document_id="assignment-inline",
                title=title,
                source_path="<inline>",
                extracted_text=request.text,
                created_at=datetime.now(timezone.utc),
                warnings=[],
            )
            return extract_assignment_brief(parsed)
        raise ValueError("Either path or text is required.")

    def _assignment_brief_from_payload(request) -> AssignmentBrief:
        if getattr(request, "brief", None) is not None:
            return AssignmentBrief.model_validate(request.brief)
        return _assignment_brief_from_request(
            AssignmentExtractRequest(
                path=getattr(request, "path", None),
                text=getattr(request, "text", None),
            )
        )

    def _blueprint_sets_from_request(request: AssignmentCodeWriteRequest, dataset_profile) -> list[AssignmentCodeBlueprintSet]:
        if request.blueprints is not None:
            payload = request.blueprints if isinstance(request.blueprints, list) else [request.blueprints]
            return [AssignmentCodeBlueprintSet.model_validate(item) for item in payload]
        numbers = request.assignment_numbers or ([request.assignment_number] if request.assignment_number else [])
        if not numbers:
            raise ValueError("assignment_number, assignment_numbers, or blueprints is required.")
        invalid = [number for number in numbers if number not in {1, 2, 3}]
        if invalid:
            raise ValueError("Assignment numbers must be 1, 2, or 3.")
        return [generate_code_blueprints(number, dataset_profile=dataset_profile) for number in sorted(set(numbers))]

    def _resolve_assignment_path(raw_path: str) -> Path:
        return resolve_user_path(
            raw_path,
            base_root=configured_workspace_root,
            expected="file",
            supported_extensions=ASSIGNMENT_DOCUMENT_EXTENSIONS,
            label="Assignment document",
        )

    @application.post("/workspace/inspect")
    def workspace_inspect(request: WorkspaceInspectRequest) -> dict:
        try:
            root = _resolve_workspace_path(request.path)
            inspection = inspect_workspace(root, max_files=request.max_files)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return inspection.model_dump(mode="json")

    @application.post("/assignments/templates/plan")
    def assignment_template_plan(request: AssignmentTemplatePlanRequest) -> dict:
        try:
            plan = generate_assignment_template_plan(request.assignment_number)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return plan.model_dump(mode="json")

    @application.post("/assignments/templates/write")
    def assignment_template_write(request: AssignmentTemplateWriteRequest) -> dict:
        try:
            if request.plan is not None:
                plan = AssignmentTemplatePlan.model_validate(request.plan)
            elif request.assignment_number is not None:
                plan = generate_assignment_template_plan(request.assignment_number)
            else:
                raise ValueError("Either assignment_number or plan is required.")
            root = _resolve_workspace_write_path(request.workspace_path)
            result = write_assignment_template_plan(root, plan, overwrite=request.overwrite)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

    @application.post("/commands/suggest")
    def command_suggest(request: CommandSuggestRequest) -> dict:
        try:
            workdir = _resolve_workspace_path(request.working_directory)
            if request.command:
                suggestion = analyze_command(
                    request.command,
                    workdir,
                    project_root=configured_workspace_root,
                )
            else:
                suggestion = suggest_command(
                    request.action or "",
                    configured_workspace_root,
                    target=request.target,
                    working_directory=workdir,
                )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return suggestion.model_dump(mode="json")

    @application.post("/assignments/commands/plan")
    def assignment_command_plan(request: AssignmentCommandPlanRequest) -> dict:
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            return plan_assignment_command(
                assignment_command_store,
                configured_workspace_root,
                workspace,
                assignment_id=request.assignment_id,
                assignment_task=request.assignment_task,
                expected_result=request.expected_result,
                action=request.action,
                target=request.target,
                timeout_seconds=request.timeout_seconds,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/assignments/commands/{plan_id}/approve")
    def assignment_command_approve(
        plan_id: str,
        request: AssignmentCommandApprovalRequest,
    ) -> dict:
        _require_chat_command_association(request.chat_run_id, plan_id)
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            plan, approval_token = approve_assignment_command(
                assignment_command_store,
                plan_id,
                assignment_id=request.assignment_id,
                workspace=workspace,
                project_root=configured_workspace_root,
                confirmation=request.confirmation,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        if request.chat_run_id is not None:
            repository.update_chat_run_action_for_plan(
                request.chat_run_id,
                plan_id,
                {
                    "status": "approved",
                    "result_summary": None,
                    "technical_details": {"command_plan": plan},
                },
            )

        return {"plan": plan, "approval_token": approval_token}

    @application.post("/assignments/commands/{plan_id}/execute")
    def assignment_command_execute(
        plan_id: str,
        request: AssignmentCommandExecuteRequest,
    ) -> dict:
        _require_chat_command_association(request.chat_run_id, plan_id)
        raise HTTPException(
            status_code=503,
            detail={
                "schema_version": "astra.legacy-execution-retired.v1",
                "code": "legacy_host_execution_retired",
                "message": (
                    "Assignment command execution runs directly on the host and has "
                    "been retired pending canonical Docker-isolated worker "
                    "integration. No project code was executed on the host."
                ),
            },
        )
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            result = execute_assignment_command(
                assignment_command_store,
                configured_workspace_root,
                plan_id,
                assignment_id=request.assignment_id,
                workspace=workspace,
                approval_token=request.approval_token,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        if request.chat_run_id is not None:
            summary, error_tail = _chat_command_result_presentation(result)
            succeeded = (
                result.get("exit_code") == 0
                and result.get("display_state") == "completed"
            )
            repository.update_chat_run_action_for_plan(
                request.chat_run_id,
                plan_id,
                {
                    "status": "completed" if succeeded else "failed",
                    "result_summary": summary,
                    "error": error_tail,
                    "technical_details": {"command_plan": result},
                },
            )

        return result

    @application.post("/assignments/commands/{plan_id}/cancel")
    def assignment_command_cancel(
        plan_id: str,
        request: AssignmentCommandAssociationRequest,
    ) -> dict:
        _require_chat_command_association(request.chat_run_id, plan_id)
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            result = cancel_assignment_command(
                assignment_command_store,
                plan_id,
                assignment_id=request.assignment_id,
                workspace=workspace,
                project_root=configured_workspace_root,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        if request.chat_run_id is not None:
            repository.update_chat_run_action_for_plan(
                request.chat_run_id,
                plan_id,
                {
                    "status": "cancelled",
                    "result_summary": (
                        "Action cancelled. No command was executed."
                    ),
                    "error": None,
                    "technical_details": {"command_plan": result},
                },
            )

        return result

    @application.get("/assignments/commands/{plan_id}")
    def assignment_command_status(
        plan_id: str,
        assignment_id: str = Query(..., min_length=1, max_length=128),
        workspace_path: str = Query(..., min_length=1),
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(workspace_path)
            return get_assignment_command(
                assignment_command_store,
                plan_id,
                project_root=configured_workspace_root,
                assignment_id=assignment_id,
                workspace=workspace,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.get("/assignments/commands/{plan_id}/logs")
    def assignment_command_logs(
        plan_id: str,
        assignment_id: str = Query(..., min_length=1, max_length=128),
        workspace_path: str = Query(..., min_length=1),
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(workspace_path)
            record = get_assignment_command(
                assignment_command_store,
                plan_id,
                project_root=configured_workspace_root,
                assignment_id=assignment_id,
                workspace=workspace,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "plan_id": record["plan_id"],
            "status": record["status"],
            "exit_code": record["exit_code"],
            "stdout": record["stdout"],
            "stderr": record["stderr"],
            "log_truncated": record["log_truncated"],
            "timed_out": record.get("timed_out", False),
            "error": record["error"],
        }

    @application.get("/assignments/{assignment_id}/execution/suggestions")
    def assignment_execution_suggestions(
        assignment_id: str,
        workspace_path: str = Query(..., min_length=1),
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(workspace_path)
            suggestions = suggest_assignment_actions(configured_workspace_root, workspace)
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "assignment_id": assignment_id,
            "workspace": workspace.relative_to(configured_workspace_root).as_posix(),
            "suggestions": suggestions,
            "executed": False,
        }

    @application.get("/assignments/{assignment_id}/execution")
    def assignment_execution_summary(
        assignment_id: str,
        workspace_path: str = Query(..., min_length=1),
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(workspace_path)
            return get_assignment_execution_summary(
                assignment_command_store,
                configured_workspace_root,
                assignment_id,
                workspace,
            )
        except (CommandExecutionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/assignments/{assignment_id}/verify")
    def assignment_verify(
        assignment_id: str,
        request: AssignmentVerifyRequest,
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            snapshot = verify_assignment_workspace(
                metadata_root=assignment_verification_store,
                command_store_root=assignment_command_store,
                project_root=configured_workspace_root,
                assignment_id=assignment_id,
                workspace=workspace,
                assignment_output=request.assignment_output,
            )
        except (AssignmentVerificationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return snapshot.model_dump(mode="json")

    @application.get("/assignments/{assignment_id}/evidence")
    def assignment_evidence(
        assignment_id: str,
        workspace_path: str = Query(..., min_length=1),
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(workspace_path)
            relative = workspace.relative_to(configured_workspace_root).as_posix()
            snapshot = load_verification_snapshot(
                assignment_verification_store, assignment_id, relative
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (AssignmentVerificationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "assignment_id": snapshot.assignment_id,
            "workspace": snapshot.workspace,
            "verification_timestamp": snapshot.verification_timestamp,
            "inventory": snapshot.inventory,
            "requirements": snapshot.requirements,
            "manual_reviews": snapshot.manual_reviews,
            "warnings": snapshot.warnings,
        }

    @application.get("/assignments/{assignment_id}/readiness")
    def assignment_readiness_v2(
        assignment_id: str,
        workspace_path: str = Query(..., min_length=1),
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(workspace_path)
            relative = workspace.relative_to(configured_workspace_root).as_posix()
            snapshot = load_verification_snapshot(
                assignment_verification_store, assignment_id, relative
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (AssignmentVerificationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return snapshot.readiness.model_dump(mode="json")

    @application.post("/assignments/{assignment_id}/evidence/review")
    def assignment_evidence_review(
        assignment_id: str,
        request: AssignmentEvidenceReviewRequest,
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            relative = workspace.relative_to(configured_workspace_root).as_posix()
            review = record_manual_evidence_review(
                metadata_root=assignment_verification_store,
                assignment_id=assignment_id,
                workspace_relative=relative,
                requirement_id=request.requirement_id,
                evidence_reference=request.evidence_reference,
                decision=request.decision,
                note=request.note,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (AssignmentVerificationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"recorded": True, "review": review.model_dump(mode="json")}

    def _report_workspace(raw_path: str) -> tuple[Path, str]:
        workspace = _resolve_workspace_path(raw_path)
        return workspace, workspace.relative_to(configured_workspace_root).as_posix()

    @application.post("/assignments/{assignment_id}/reports")
    def assignment_report_create(assignment_id: str, request: AssignmentReportCreateRequest) -> dict:
        try:
            _, relative = _report_workspace(request.workspace_path)
            report = create_grounded_report(
                report_root=assignment_report_store,
                verification_root=assignment_verification_store,
                assignment_id=assignment_id,
                workspace_relative=relative,
                title=request.title,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, AssignmentVerificationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return report.model_dump(mode="json")

    @application.get("/assignments/{assignment_id}/reports")
    def assignment_report_list(assignment_id: str, workspace_path: str = Query(..., min_length=1)) -> dict:
        try:
            _, relative = _report_workspace(workspace_path)
            reports = list_grounded_reports(assignment_report_store, assignment_id, relative)
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"assignment_id": assignment_id, "workspace": relative, "reports": [item.model_dump(mode="json") for item in reports]}

    @application.get("/assignments/{assignment_id}/reports/{report_id}")
    def assignment_report_get(assignment_id: str, report_id: str, workspace_path: str = Query(..., min_length=1)) -> dict:
        try:
            _, relative = _report_workspace(workspace_path)
            return load_grounded_report(assignment_report_store, assignment_id, relative, report_id).model_dump(mode="json")
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.patch("/assignments/{assignment_id}/reports/{report_id}")
    def assignment_report_update(assignment_id: str, report_id: str, request: AssignmentReportUpdateRequest) -> dict:
        try:
            _, relative = _report_workspace(request.workspace_path)
            report = update_grounded_report(report_root=assignment_report_store, assignment_id=assignment_id, workspace_relative=relative, report_id=report_id, changes=request.changes)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return report.model_dump(mode="json")

    @application.post("/assignments/{assignment_id}/reports/{report_id}/assemble")
    def assignment_report_assemble(assignment_id: str, report_id: str, request: AssignmentReportWorkspaceRequest) -> dict:
        try:
            _, relative = _report_workspace(request.workspace_path)
            report = assemble_grounded_report(report_root=assignment_report_store, verification_root=assignment_verification_store, assignment_id=assignment_id, workspace_relative=relative, report_id=report_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, AssignmentVerificationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return report.model_dump(mode="json")

    @application.get("/assignments/{assignment_id}/reports/{report_id}/readiness")
    def assignment_report_readiness(assignment_id: str, report_id: str, workspace_path: str = Query(..., min_length=1)) -> dict:
        try:
            _, relative = _report_workspace(workspace_path)
            report = load_grounded_report(assignment_report_store, assignment_id, relative, report_id)
            return report_export_readiness(report).model_dump(mode="json")
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/assignments/{assignment_id}/reports/{report_id}/export")
    def assignment_report_export(assignment_id: str, report_id: str, request: AssignmentReportExportRequestV2) -> dict:
        try:
            _, relative = _report_workspace(request.workspace_path)
            record = export_grounded_report(report_root=assignment_report_store, project_root=configured_workspace_root, assignment_id=assignment_id, workspace_relative=relative, report_id=report_id, export_format=request.format, selected_files=request.selected_files)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return record.model_dump(mode="json")

    @application.get("/assignments/{assignment_id}/reports/{report_id}/exports")
    def assignment_report_exports(assignment_id: str, report_id: str, workspace_path: str = Query(..., min_length=1)) -> dict:
        try:
            _, relative = _report_workspace(workspace_path)
            records = list_report_exports(assignment_report_store, assignment_id, relative, report_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"exports": [record.model_dump(mode="json") for record in records]}

    @application.get("/assignments/{assignment_id}/reports/{report_id}/exports/{export_id}")
    def assignment_report_export_download(assignment_id: str, report_id: str, export_id: str, workspace_path: str = Query(..., min_length=1)):
        try:
            _, relative = _report_workspace(workspace_path)
            record, target = resolve_report_export(assignment_report_store, assignment_id, relative, report_id, export_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReportAssemblyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return FileResponse(path=target, media_type=record.media_type, filename=record.filename)

    @application.post("/debug/analyze-error")
    def debug_analyze_error(request: DebugAnalyzeErrorRequest) -> dict:
        try:
            root = _resolve_workspace_path(request.project_path)
            analysis = analyze_error_output(request.output, project_root=root)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return analysis.model_dump(mode="json")

    def _resolve_workspace_path(raw_path: str | None) -> Path:
        return resolve_user_path(
            raw_path,
            base_root=configured_workspace_root,
            expected="directory",
            label="Workspace path",
        )

    def _resolve_workspace_write_path(raw_path: str | None) -> Path:
        return resolve_user_path(
            raw_path,
            base_root=configured_workspace_root,
            expected="directory",
            label="Workspace path",
            require_exists=False,
        )

    def _resolve_dataset_path(raw_path: str) -> Path:
        return resolve_user_path(
            raw_path,
            base_root=configured_workspace_root,
            expected="file",
            supported_extensions={".csv", ".txt", ".tsv"},
            label="Dataset file",
        )

    @application.post("/rag/index")
    def local_rag_index() -> dict:
        return rag_build_project_index(configured_workspace_root)

    @application.get("/rag/index/status")
    def local_rag_index_status() -> dict:
        return rag_project_index_status(configured_workspace_root)

    @application.get("/rag/files")
    def local_rag_files() -> dict:
        return rag_indexed_files(configured_workspace_root)

    @application.post("/rag/search")
    def local_rag_search(request: RAGSearchRequest) -> dict:
        return rag_search(
            configured_workspace_root,
            query=request.query,
            limit=request.limit,
            source_filter=request.source_filter,
        )

    @application.post("/rag/evaluate")
    def local_rag_evaluate(request: RAGEvaluationRequest | None = None) -> dict:
        return evaluate_project_rag(
            configured_workspace_root,
            selected_cases=request.selected_cases if request else None,
        )

    @application.get("/rag/evaluation/status")
    def local_rag_evaluation_status() -> dict:
        return rag_evaluation_status(configured_workspace_root)

    @application.post("/slm/chat-with-context")
    def slm_chat_with_context(request: SLMChatWithContextRequest) -> dict:
        search = rag_search(
            configured_workspace_root,
            query=request.message,
            limit=request.limit,
            source_filter=request.source_filter,
        )
        response = chat_with_slm(
            request.message,
            {
                "rag_context": compact_context(search["results"]),
                "sources": search["results"],
            },
        )
        return {
            **response,
            "context_results": search["results"],
            "citations": [
                {"path": item.get("path"), "source": item.get("source")}
                for item in search["results"]
            ],
        }

    _PYTEST_TOTAL_PATTERN = re.compile(
        r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warning|warnings)\b"
    )
    _PYTEST_DURATION_PATTERN = re.compile(
        r"\bin\s+([0-9]+(?:\.[0-9]+)?)s\b"
    )

    def _chat_command_result_presentation(
        command: dict,
    ) -> tuple[str, str | None]:
        """Create a concise persisted command result and relevant error tail."""
        stdout = str(command.get("stdout") or "")
        stderr = str(command.get("stderr") or "")
        output = "\n".join(item for item in (stdout, stderr) if item)

        summary = ""
        if command.get("action") == "pytest":
            lines = [
                line.strip()
                for line in output.splitlines()
                if line.strip()
            ]
            summary_line = next(
                (
                    line
                    for line in reversed(lines)
                    if _PYTEST_DURATION_PATTERN.search(line)
                    and re.search(
                        r"\b(?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b",
                        line,
                    )
                ),
                None,
            )
            if summary_line is not None:
                totals = [
                    (int(match.group(1)), match.group(2))
                    for match in _PYTEST_TOTAL_PATTERN.finditer(summary_line)
                ]
                duration_match = _PYTEST_DURATION_PATTERN.search(summary_line)
                if totals and duration_match:
                    duration = duration_match.group(1)
                    if len(totals) == 1 and totals[0][1] == "passed":
                        count = totals[0][0]
                        noun = "test" if count == 1 else "tests"
                        summary = f"{count} {noun} passed in {duration} seconds."
                    else:
                        descriptions = [
                            f"{count} {label}"
                            for count, label in totals
                        ]
                        if len(descriptions) == 1:
                            joined = descriptions[0]
                        elif len(descriptions) == 2:
                            joined = " and ".join(descriptions)
                        else:
                            joined = (
                                ", ".join(descriptions[:-1])
                                + f", and {descriptions[-1]}"
                            )
                        summary = (
                            f"Pytest finished with {joined} "
                            f"in {duration} seconds."
                        )

        succeeded = (
            command.get("exit_code") == 0
            and command.get("display_state") == "completed"
        )
        if not summary:
            if succeeded:
                summary = (
                    "Command completed successfully with exit code "
                    f"{command.get('exit_code')}."
                )
            else:
                summary = (
                    "Command failed with exit code "
                    f"{command.get('exit_code', 'unavailable')}."
                )

        error_source = str(
            command.get("error")
            or stderr
            or stdout
            or ""
        )
        error_lines = [
            line.rstrip()
            for line in error_source.splitlines()
            if line.strip()
        ]
        error_tail = None if succeeded else "\n".join(error_lines[-8:]) or None
        return summary, error_tail

    def _require_chat_command_association(
        chat_run_id: str | None,
        plan_id: str,
    ) -> None:
        if chat_run_id is None:
            return
        if not repository.chat_run_action_matches_plan(chat_run_id, plan_id):
            raise HTTPException(
                status_code=409,
                detail="The chat run is not associated with this command plan.",
            )

    def _require_assignment_action_association(
        chat_run_id: str,
        action_id: str,
    ) -> None:
        if not repository.chat_run_action_matches_id(chat_run_id, action_id):
            raise HTTPException(
                status_code=409,
                detail="The chat run is not associated with this assignment action.",
            )

    def _require_folder_action_association(
        chat_run_id: str,
        action_id: str,
    ) -> None:
        if not repository.chat_run_action_matches_id(chat_run_id, action_id):
            raise HTTPException(
                status_code=409,
                detail="The chat run is not associated with this folder action.",
            )

    def _folder_action_from_run(run: ChatRunResponse) -> dict:
        action = run.action or {}
        if action.get("action_type") != "folder_access":
            raise HTTPException(status_code=409, detail="Chat run does not contain a folder action.")
        technical = action.get("technical_details")
        folder_action = (
            technical.get("folder_action")
            if isinstance(technical, dict)
            else None
        )
        if not isinstance(folder_action, dict) or folder_action.get("action_id") != action.get("action_id"):
            raise HTTPException(status_code=400, detail="Folder action is malformed.")
        return folder_action

    def _completed_project_access(conversation_id: str) -> dict:
        turns = repository.list_chat_runs_for_conversation(conversation_id)
        access = completed_folder_access(turns)
        if access is None:
            raise HTTPException(
                status_code=409,
                detail="This conversation does not have an approved connected project folder.",
            )
        root = str(access.get("approved_root") or "")
        fingerprint = str(access.get("root_fingerprint") or "")
        if not fingerprint:
            raise HTTPException(status_code=409, detail="The folder access predates secure project-content approval. Reconnect the folder.")
        try:
            validate_root_identity(root, fingerprint)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="The approved project folder no longer exists.") from error
        except (ProjectSafetyError, FolderScanError, OSError) as error:
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        return access

    def _optional_project_access(conversation_id: str) -> dict | None:
        turns = repository.list_chat_runs_for_conversation(conversation_id)
        if completed_folder_access(turns) is None:
            return None
        return _completed_project_access(conversation_id)

    def _start_client_engagement(message: str, conversation_id: str, access: dict | None) -> ChatRunResponse:
        engagement = engagement_service.create(
            conversation_id=conversation_id, original_request=message, user_id="local-user",
            folder_root=str(access["approved_root"]) if access else None,
            folder_access_id=str(access["action_id"]) if access else None,
            idempotency_key=f"chat:{conversation_id}:{hashlib.sha256(message.encode('utf-8')).hexdigest()}",
        )
        return build_engagement_chat_run(engagement, message=message)

    def _is_scope_change_message(message: str) -> bool:
        normalized = " ".join(str(message or "").lower().split())
        return bool(re.search(r"^(?:please\s+)?(?:add|include|support|remove|drop|change)\b", normalized))

    def _validated_engagement(engagement_id: str, conversation_id: str) -> dict:
        try:
            engagement = repository.get_client_engagement(engagement_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Client engagement not found."}) from error
        if engagement.get("conversation_id") != conversation_id:
            raise HTTPException(status_code=409, detail={"code": "ownership_mismatch", "message": "The engagement belongs to a different conversation."})
        if engagement.get("folder_access_id"):
            access = _completed_project_access(conversation_id)
            if access.get("action_id") != engagement.get("folder_access_id"):
                raise HTTPException(status_code=409, detail={"code": "workspace_mismatch", "message": "The engagement belongs to a different folder authorization."})
        return engagement

    def _engagement_run(engagement: dict, message: str) -> ChatRunResponse:
        run = build_engagement_chat_run(engagement, message=message)
        repository.store_chat_run(run)
        _sync_engagement_action(engagement)
        return run

    def _sync_engagement_action(engagement: dict) -> None:
        from backend.app.client_engagement import build_engagement_action
        action = build_engagement_action(engagement)
        for run in repository.list_chat_runs_for_conversation(str(engagement["conversation_id"])):
            if isinstance(run.action, dict) and run.action.get("action_id") == engagement.get("engagement_id"):
                repository.update_chat_run_action_for_id(run.run_id, str(engagement["engagement_id"]), action)

    def _engagement_http_error(error: EngagementError) -> HTTPException:
        status = 404 if error.code == "not_found" else 400 if error.code in {"invalid_request", "hash_mismatch"} else 409
        return HTTPException(status_code=status, detail={"code": error.code, "message": str(error)})

    def _launch_engagement_delivery(engagement: dict, revision: EngagementScopeRevision, access: dict) -> dict:
        task = stage9_task_from_scope(revision)
        run = _start_project_delivery(task, str(engagement["conversation_id"]), access, persist_run=False)
        details = (run.action or {}).get("technical_details", {}).get("project_delivery", {})
        delivery_id = str(details.get("delivery_job_id") or "")
        delivery = repository.get_project_delivery_job(delivery_id)
        linked = json.loads(json.dumps(delivery))
        linked["client_engagement"] = {
            "engagement_id": engagement["engagement_id"], "scope_revision_id": revision.revision_id,
            "scope_hash": revision.scope_hash,
            "acceptance_criterion_map": {
                criterion.criterion_id: criterion.statement
                for deliverable in revision.scope.deliverables
                for criterion in deliverable.acceptance_criteria
            },
            "evidence_references": sorted({value for refs in revision.scope.evidence_traceability.values() for value in refs}),
        }
        linked["updated_at"] = datetime.now(timezone.utc).isoformat()
        stored = repository.transition_project_delivery_job(linked, expected_version=int(delivery.get("state_version") or 1))
        if stored is None:
            raise EngagementError("The Stage 9 project changed concurrently during launch.", code="conflict")
        _persist_delivery_records(stored)
        _delivery_audit(stored, "client_engagement_launch", "linked", linked["client_engagement"])
        return stored

    def _notify_delivery_scope_change(before: dict, after: dict) -> None:
        launch = before.get("project_launch") or {}
        delivery_id = str(launch.get("delivery_job_id") or "")
        if not delivery_id:
            return
        try:
            delivery, _access = _validated_project_delivery(delivery_id, conversation_id=str(before["conversation_id"]))
            change = (after.get("scope_changes") or [])[-1]
            revised = record_delivery_scope_change(
                delivery, work_unit_id=None, reason_code=str(change.get("classification") or "engagement_scope_change"),
                explanation=str(change.get("requested_change") or "Approved Stage 10 scope revision."),
                affected_paths=[], material=True,
            )
            _save_delivery_transition(delivery, revised, "engagement_scope_change", "replanning_required", {"engagement_id": after["engagement_id"], "revision_id": after.get("approved_scope_revision_id")})
        except (ProjectDeliveryError, HTTPException):
            raise EngagementError("The revised scope was approved, but the linked Stage 9 project could not be notified safely.", code="scope_change_notification_failed")

    def _start_project_delivery(
        message: str,
        conversation_id: str,
        access: dict,
        *,
        persist_run: bool,
    ) -> ChatRunResponse:
        action_run_id = uuid4().hex
        delivery_job_id = uuid4().hex
        folder_authority = {
            "status": "completed",
            "action_id": str(access["action_id"]),
            "conversation_id": conversation_id,
            "workspace_id": str(access["action_id"]),
            "repository_root_fingerprint": str(access["root_fingerprint"]),
            "repository_root": str(access["approved_root"]),
        }
        try:
            canonical_project_service.initialize_project(
                project_run_id=delivery_job_id,
                conversation_id=conversation_id,
                workspace_id=str(access["action_id"]),
                repository_root=str(access["approved_root"]),
                repository_root_fingerprint=str(access["root_fingerprint"]),
                actor_id="local-user",
                idempotency_key=f"delivery-create:{action_run_id}",
                folder_authority=folder_authority,
            )
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        try:
            delivery = create_delivery_job(
                root=access["approved_root"], conversation_id=conversation_id,
                folder_access_id=access["action_id"], user_request=message,
                action_run_id=action_run_id, model_gateway=None,
                delivery_job_id=delivery_job_id,
            )
            shadow = create_project_job(
                root=access["approved_root"], conversation_id=conversation_id,
                folder_access_id=access["action_id"], user_task=message,
                action_run_id=f"delivery-shadow-{action_run_id}",
            )
        except IncompleteProjectManifestError as error:
            _stage0_audit("project_manifest", str(access.get("action_id") or "unknown"), "incomplete_manifest_rejected", "rejected", {"error_code": error.code})
            raise HTTPException(status_code=409, detail={"code": error.code, "message": str(error)}) from error
        except (ProjectDeliveryError, ProjectJobError, ProjectAnalysisError, ProjectManifestError, ProjectSafetyError, OSError) as error:
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        shadow.update({
            "status": "planned", "delivery_job_id": delivery["delivery_job_id"],
            "clarification": {"question": None, "answer": None, "requested_at": None, "answered_at": None},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        delivery["project_job_id"] = shadow["job_id"]
        specification = dict(delivery["specification"])
        plan = dict(delivery["plan"]) if delivery.get("plan") else None
        if plan is not None:
            plan["acceptance_criteria"] = list(
                specification.get("acceptance_criteria") or []
            )
            plan["configured_limits"] = dict(delivery.get("limits") or {})
        try:
            canonical_project_service.create_project(
                project_run_id=delivery_job_id,
                conversation_id=conversation_id,
                workspace_id=str(access["action_id"]),
                repository_root=str(access["approved_root"]),
                repository_root_fingerprint=str(access["root_fingerprint"]),
                actor_id="local-user",
                idempotency_key=f"delivery-create:{action_run_id}",
                folder_authority=folder_authority,
                specification=specification,
                manifest=dict(delivery["project_state_manifest"]),
                plan=plan,
            )
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        repository.store_project_job(shadow)
        repository.store_project_analysis(shadow["analysis_index"])
        repository.store_project_delivery_job(delivery)
        stored = repository.get_project_delivery_job(str(delivery["delivery_job_id"]))
        try:
            stored = delivery_control.decorate(stored, access["approved_root"], migrated=False)
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        _persist_delivery_records(stored)
        _delivery_audit(stored, "job_creation", "created", {
            "specification_hash": stored["specification"]["specification_hash"],
            "plan_hash": (stored.get("plan") or {}).get("plan_hash"),
        })
        _delivery_audit(stored, "project_manifest_created", "completed", {
            "manifest_hash": stored.get("project_state_hash"),
            "entry_count": len((stored.get("project_state_manifest") or {}).get("entries") or []),
        })
        if stored.get("plan_revision"):
            _delivery_audit(stored, "plan_revision_created", "completed", {
                "plan_revision_id": stored["plan_revision"]["plan_revision_id"],
                "content_hash": stored["plan_revision"]["content_hash"],
            })
        run = build_delivery_chat_run(stored, message=message, run_id=action_run_id)
        if persist_run:
            repository.store_chat_run(run)
        return run

    def _read_project_delivery(
        delivery_job_id: str,
        *,
        conversation_id: str | None = None,
    ) -> tuple[dict, dict]:
        """Read and authorize a compatibility projection without migration or writes."""
        try:
            job = repository.get_project_delivery_job(delivery_job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project delivery job not found."}) from error
        if conversation_id is not None and job.get("conversation_id") != conversation_id:
            raise HTTPException(status_code=409, detail={"code": "ownership_mismatch", "message": "The delivery job belongs to a different conversation."})
        access = _completed_project_access(str(job.get("conversation_id") or ""))
        if job.get("folder_access_id") != access.get("action_id"):
            raise HTTPException(status_code=409, detail={"code": "workspace_mismatch", "message": "The delivery job belongs to a different folder authorization."})
        if job.get("root_fingerprint") != access.get("root_fingerprint"):
            raise HTTPException(status_code=409, detail={"code": "stale_workspace", "message": "The authorized project identity changed."})
        return job, access

    def _public_read_delivery(job: dict) -> dict:
        payload = public_delivery_job(job)
        try:
            # Hydration is read-only, but action authority must come from the
            # same current canonical projection as the displayed lifecycle.
            # ``decorate`` performs no reconciliation or persistence.
            payload = delivery_control.decorate(payload, "", migrated=False)
            active_intents = [
                item for item in project_coordinator.list_for_project(
                    str(job["delivery_job_id"])
                )
                if item.status.value in {"pending", "claimed"}
            ]
            if active_intents:
                payload.setdefault("compatibility_action_binding", {})[
                    "coordinator_intent_id"
                ] = active_intents[-1].coordinator_intent_id
        except ProjectControlError as error:
            if error.code != ProjectControlErrorCode.PROJECT_NOT_FOUND:
                raise _control_http_error(error) from error
        return payload

    def _validated_project_delivery(
        delivery_job_id: str,
        *,
        conversation_id: str | None = None,
    ) -> tuple[dict, dict]:
        if delivery_control.classification.is_historical_read_only(delivery_job_id):
            raise _control_http_error(ProjectControlError(
                ProjectControlErrorCode.HISTORICAL_RECORD_READ_ONLY,
                "Historical project delivery records are read-only; explicitly import and reapprove them before mutation.",
            ))
        try:
            job = repository.get_project_delivery_job(delivery_job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project delivery job not found."}) from error
        if conversation_id is not None and job.get("conversation_id") != conversation_id:
            raise HTTPException(status_code=409, detail={"code": "ownership_mismatch", "message": "The delivery job belongs to a different conversation."})
        adapted = adapt_legacy_delivery_job(job)
        if adapted is not job:
            stored = repository.transition_project_delivery_job(
                adapted, expected_version=int(job.get("state_version") or 1)
            )
            job = stored or repository.get_project_delivery_job(delivery_job_id)
            if stored is not None:
                _persist_delivery_records(job)
                operation = "legacy_approval_reapproval_required" if (job.get("legacy_migration") or {}).get("legacy_approval_discarded") else "legacy_plan_adapted"
                _delivery_audit(job, operation, "reapproval_required" if operation.startswith("legacy_approval") else "completed", {})
        access = _completed_project_access(str(job.get("conversation_id") or ""))
        if job.get("folder_access_id") != access.get("action_id"):
            raise HTTPException(status_code=409, detail={"code": "workspace_mismatch", "message": "The delivery job belongs to a different folder authorization."})
        if job.get("root_fingerprint") != access.get("root_fingerprint"):
            raise HTTPException(status_code=409, detail={"code": "stale_workspace", "message": "The authorized project identity changed."})
        try:
            job = delivery_control.decorate(job, access["approved_root"], migrated=True)
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        return job, access

    def _save_delivery_transition(
        current: dict,
        updated: dict,
        operation: str,
        status: str,
        metadata: dict | None = None,
    ) -> dict:
        try:
            access = _completed_project_access(str(current.get("conversation_id") or ""))
            if not (metadata or {}).get("canonical_preapplied"):
                delivery_control.apply_transition(
                    current, updated, access["approved_root"], operation, metadata,
                )
        except ProjectControlError as error:
            raise _control_http_error(error) from error
        expected_version = int(current.get("state_version") or 1)
        stored = repository.transition_project_delivery_job(updated, expected_version=expected_version)
        if stored is None:
            raise HTTPException(status_code=409, detail={"code": "conflict", "message": "The delivery job changed concurrently; reload its current state."})
        _persist_delivery_records(stored)
        _delivery_audit(stored, operation, status, metadata or {})
        try:
            project_coordinator.reconcile(str(stored["delivery_job_id"]))
        except CoordinatorIntentError:
            pass
        try:
            return delivery_control.decorate(stored, access["approved_root"], migrated=False)
        except ProjectControlError as error:
            raise _control_http_error(error) from error

    def _validate_delivery_action_binding(job: dict, request: ProjectJobActionRequest) -> None:
        control = dict(job.get("project_control") or {})
        checks = (
            (request.project_run_id, job.get("delivery_job_id"), "project_run_id"),
            (request.workspace_id, control.get("workspace_id"), "workspace_id"),
            (request.actor_id, control.get("actor_id"), "actor_id"),
            (request.repository_root_fingerprint, control.get("repository_root_fingerprint"), "repository_root_fingerprint"),
            (request.plan_revision_id, control.get("plan_revision_id"), "plan_revision_id"),
            (request.scope_revision_id, control.get("scope_revision_id"), "scope_revision_id"),
            (request.manifest_hash, control.get("manifest_hash"), "manifest_hash"),
        )
        for supplied, current, field in checks:
            if supplied is None:
                raise HTTPException(status_code=422, detail={
                    "schema_version": "astra.project-control.error.v1",
                    "code": "invalid_command",
                    "message": f"Compatibility mutations require the exact {field.replace('_', ' ')}.",
                })
            if supplied != current:
                raise HTTPException(status_code=409, detail={
                    "schema_version": "astra.project-control.error.v1",
                    "code": f"{field.replace('_id', '')}_mismatch",
                    "message": f"The action targets a stale {field.replace('_', ' ')}.",
                })
        if request.expected_state_version is None or request.idempotency_key is None:
            raise HTTPException(status_code=422, detail={
                "schema_version": "astra.project-control.error.v1",
                "code": "invalid_command",
                "message": "Compatibility mutations require exact state-version and idempotency bindings.",
            })
        if request.expected_state_version != control.get("state_version"):
            raise HTTPException(status_code=409, detail={
                "schema_version": "astra.project-control.error.v1",
                "code": "stale_state_version",
                "message": "The project changed after this action was displayed; reload the current card.",
                "metadata": {"current": control.get("state_version")},
            })

    def _persist_delivery_records(job: dict) -> None:
        values: list[tuple[str, dict, str]] = []
        specification = job.get("specification")
        if isinstance(specification, dict) and specification.get("specification_hash"):
            values.append(("task_specification", specification, str(specification["specification_hash"])))
        plan = job.get("plan")
        if isinstance(plan, dict) and plan.get("plan_hash"):
            values.append(("execution_plan", plan, str(plan["plan_hash"])))
        plan_revision = job.get("plan_revision")
        if isinstance(plan_revision, dict) and plan_revision.get("content_hash"):
            values.append(("execution_plan_revision", plan_revision, str(plan_revision["content_hash"])))
        manifest = job.get("project_state_manifest")
        if isinstance(manifest, dict) and manifest.get("manifest_hash"):
            values.append(("project_state_manifest", manifest, str(manifest["manifest_hash"])))
        approval = job.get("plan_approval")
        if isinstance(approval, dict) and approval.get("approval_id"):
            values.append(("plan_approval", approval, str(approval["approval_id"]).ljust(64, "0")[:64]))
        for record in job.get("verification_records") or []:
            if isinstance(record, dict) and record.get("verification_hash"):
                values.append(("verification", record, str(record["verification_hash"])))
        for result in job.get("verifier_results") or []:
            if isinstance(result, dict) and result.get("result_hash"):
                values.append(("verifier_result", result, str(result["result_hash"])))
        handoff = job.get("handoff")
        if isinstance(handoff, dict) and handoff.get("handoff_hash"):
            values.append(("handoff", handoff, str(handoff["handoff_hash"])))
        for record_type, record, digest in values:
            repository.store_project_delivery_record(
                delivery_job_id=str(job["delivery_job_id"]), record_type=record_type,
                immutable_hash=digest, record=record,
            )

    def _delivery_audit(job: dict, operation: str, status: str, metadata: dict) -> None:
        safe_metadata = json.loads(json.dumps(metadata, default=str))
        for key, value in list(safe_metadata.items()):
            if any(term in key.lower() for term in ("token", "password", "secret", "authorization", "cookie", "source", "output")):
                safe_metadata[key] = "[REDACTED]"
            elif isinstance(value, str):
                safe_metadata[key] = value[:1000]
        repository.store_project_delivery_audit_event({
            "event_id": uuid4().hex, "delivery_job_id": job["delivery_job_id"],
            "conversation_id": job["conversation_id"], "operation": operation,
            "status": status, "metadata": safe_metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _stage0_audit(domain: str, aggregate_id: str, operation: str, status: str, metadata: dict) -> None:
        safe_metadata = json.loads(json.dumps(metadata, default=str))
        for key, value in list(safe_metadata.items()):
            if any(term in key.lower() for term in ("token", "password", "secret", "authorization", "cookie", "content", "output")):
                safe_metadata[key] = "[REDACTED]"
            elif isinstance(value, str):
                safe_metadata[key] = value[:1000]
        repository.store_stage0_audit_event({
            "event_id": uuid4().hex, "domain": domain[:80], "aggregate_id": aggregate_id[:160],
            "operation": operation[:120], "status": status[:80], "metadata": safe_metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _sync_delivery_action(job: dict) -> None:
        action = build_delivery_action(job)
        updated = 0
        for run in repository.list_chat_runs_for_conversation(str(job["conversation_id"])):
            if isinstance(run.action, dict) and run.action.get("action_id") == job.get("delivery_job_id"):
                updated += int(repository.update_chat_run_action_for_id(run.run_id, str(job["delivery_job_id"]), action))
        if updated == 0:
            raise HTTPException(status_code=409, detail={"code": "missing_action_card", "message": "The persisted delivery card could not be updated."})

    def _delivery_http_error(error: ProjectDeliveryError | ProjectVerifierError | ProjectManifestError) -> HTTPException:
        status_code = 400 if error.code in {"invalid_request", "hash_mismatch"} else 409
        return HTTPException(status_code=status_code, detail={"code": error.code, "message": str(error)})

    def _control_http_error(error: ProjectControlError) -> HTTPException:
        status_code = 404 if error.code == ProjectControlErrorCode.PROJECT_NOT_FOUND else 409
        return HTTPException(status_code=status_code, detail=error.as_dict())

    def _delivery_for_patch(proposal: dict) -> tuple[dict, dict] | None:
        delivery_id = proposal.get("delivery_job_id")
        if not delivery_id:
            return None
        job, access = _validated_project_delivery(str(delivery_id), conversation_id=str(proposal.get("conversation_id") or ""))
        if not any(item.get("patch_id") == proposal.get("patch_id") for item in job.get("patch_references") or []):
            raise HTTPException(status_code=409, detail={"code": "ownership_mismatch", "message": "The patch is not associated with this delivery job."})
        return job, access

    def _validated_project_job(
        job_id: str,
        *,
        conversation_id: str | None = None,
    ) -> tuple[dict, dict]:
        try:
            job = repository.get_project_job(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Project job not found.") from error
        if conversation_id is not None and job.get("conversation_id") != conversation_id:
            raise HTTPException(status_code=409, detail="The project job belongs to a different conversation.")
        access = _completed_project_access(str(job.get("conversation_id") or ""))
        if job.get("folder_access_id") != access.get("action_id"):
            raise HTTPException(status_code=409, detail="The project job belongs to a different folder access.")
        if job.get("root_fingerprint") != access.get("root_fingerprint"):
            raise HTTPException(status_code=409, detail="The approved project root identity has changed for this job.")
        return job, access

    def _validated_project_analysis(job: dict) -> dict:
        analysis_id = str(job.get("analysis_id") or "")
        try:
            index = repository.get_project_analysis(analysis_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Project structural analysis not found.") from error
        if index.get("job_id") != job.get("job_id") or index.get("conversation_id") != job.get("conversation_id"):
            raise HTTPException(status_code=409, detail="Project analysis belongs to a different job or conversation.")
        if index.get("folder_access_id") != job.get("folder_access_id"):
            raise HTTPException(status_code=409, detail="Project analysis belongs to a different folder access.")
        if index.get("root_fingerprint") != job.get("root_fingerprint"):
            raise HTTPException(status_code=409, detail="Project analysis root binding is stale.")
        return index

    def _sync_project_job_action(job: dict) -> None:
        action = build_job_action(job)
        updated_count = 0
        for run in repository.list_chat_runs_for_conversation(str(job["conversation_id"])):
            if isinstance(run.action, dict) and run.action.get("action_id") == job.get("job_id"):
                if repository.update_chat_run_action_for_id(run.run_id, str(job["job_id"]), action):
                    updated_count += 1
        if updated_count == 0 and job.get("delivery_job_id"):
            return
        if updated_count == 0:
            raise HTTPException(status_code=409, detail="The persisted project job card could not be updated.")

    def _job_for_patch(proposal: dict) -> tuple[dict, dict] | None:
        job_id = proposal.get("job_id")
        if not job_id:
            return None
        job, access = _validated_project_job(str(job_id), conversation_id=str(proposal.get("conversation_id") or ""))
        if proposal.get("patch_id") not in job.get("patch_ids", []):
            raise HTTPException(status_code=409, detail="This patch is not associated with the project job.")
        return job, access

    def _job_id_from_command(command: dict) -> str | None:
        assignment_id = str(command.get("assignment_id") or "")
        prefix = "project-job:"
        return assignment_id[len(prefix):] if assignment_id.startswith(prefix) else None

    def _validate_job_command(plan_id: str, run: ChatRunResponse, access: dict) -> str | None:
        try:
            command = get_assignment_command(
                assignment_command_store, plan_id, project_root=access["approved_root"],
            )
        except (CommandExecutionError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
        job_id = _job_id_from_command(command)
        if not job_id:
            return None
        job, _job_access = _validated_project_job(job_id, conversation_id=run.conversation_id)
        if plan_id not in job.get("command_plan_ids", []):
            raise HTTPException(status_code=409, detail="This command is not associated with the project job.")
        return job_id
    def _canonical_execution_spec(command: dict):
        action = str(command.get("action") or "")
        if action in {"docker_ps", "docker_compose_up", "streamlit"}:
            raise CommandExecutionError(
                f"The {action or 'requested'} action is unavailable for canonical project execution."
            )
        argv = [str(item) for item in command.get("argv") or []]
        workspace = str(command.get("workspace") or ".").strip().replace("\\", "/")
        workspace = "." if workspace in {"", "."} else workspace.strip("/")

        def repository_relative(value: str) -> str:
            relative = value.strip().replace("\\", "/").strip("/")
            return relative if workspace == "." else f"{workspace}/{relative}"

        target = None
        arguments: list[str] = []
        if action == "pytest":
            arguments = ["-q"]
            if len(argv) == 5:
                arguments.append(repository_relative(argv[4]))
        elif action == "python_script":
            if len(argv) != 2:
                raise CommandExecutionError("The approved Python command shape is invalid.")
            target = repository_relative(argv[1])
        elif action == "node_test":
            if len(argv) != 3:
                raise CommandExecutionError("The approved Node test command shape is invalid.")
            target = repository_relative(argv[2])
        elif action not in {
            "npm_test", "npm_run_lint", "npm_run_build", "npm_run_typecheck"
        }:
            raise CommandExecutionError("The approved action is unsupported by the isolated runtime.")

        artifacts = tuple(
            ExecutionInputArtifact(
                relative_path=repository_relative(str(item.get("path") or "")),
                sha256=str(item.get("sha256") or ""),
            )
            for item in command.get("approved_artifacts") or []
            if isinstance(item, dict)
        )
        profile = default_isolation_profile()
        return build_execution_spec(
            action=action,
            command_id=str(command.get("plan_id") or ""),
            working_directory=workspace,
            target=target,
            arguments=arguments,
            input_artifacts=artifacts,
            isolation_profile_id=profile.profile_id,
            image_digest=profile.image_digest,
        )

    def _canonical_command_dispatch(command: dict, execution_spec) -> dict:
        plan_id = str(command.get("plan_id") or "")
        return {
            "payload": {"execution": execution_spec.model_dump(mode="json")},
            "limits": {
                "timeout_seconds": int(command.get("timeout_seconds") or 30),
                "max_output_bytes": 1_048_576,
                "max_deliveries": 1,
            },
            "idempotency_key": f"canonical-command:{plan_id}:{execution_spec.execution_hash}",
        }


    def _latest_applied_job_patch(job_id: str) -> dict:
        patches = repository.list_project_patches_for_job(job_id)
        applied = [patch for patch in patches if patch.get("status") == "applied"]
        if not applied:
            raise HTTPException(status_code=409, detail="The failed command has no applied parent patch.")
        return applied[-1]

    def _capture_project_failure(job: dict, access: dict, result: dict) -> tuple[dict, dict]:
        parent_patch = _latest_applied_job_patch(str(job["job_id"]))
        try:
            evidence_model = build_failure_evidence(
                access["approved_root"], job=job, parent_patch=parent_patch, command=result,
            )
        except (ValueError, ProjectAnalysisError, ProjectSafetyError, OSError) as error:
            raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
        evidence = repository.store_project_failure_evidence(evidence_model.model_dump(mode="json"))
        existing = repository.list_project_repair_cycles_for_job(str(job["job_id"]))
        same = next((item for item in existing if item.get("command_execution_id") == evidence["command_execution_id"]), None)
        if same is not None:
            return evidence, same
        cycle_number = len(existing) + 1
        repair = dict(job.get("repair") or {})
        chain_id = str(repair.get("repair_chain_id") or uuid4().hex)
        now = datetime.now(timezone.utc).isoformat()
        cycle = {
            "repair_cycle_id": uuid4().hex, "repair_chain_id": chain_id,
            "cycle_number": cycle_number, "job_id": job["job_id"],
            "conversation_id": job["conversation_id"], "parent_patch_id": parent_patch["patch_id"],
            "repair_patch_id": None, "command_execution_id": evidence["command_execution_id"],
            "failure_evidence_id": evidence["evidence_id"], "diagnosis_id": None,
            "synthesis_attempt_id": None, "root_fingerprint": job["root_fingerprint"],
            "project_state_hash": evidence["project_state_hash"], "analysis_id": None,
            "diagnosis_model_calls": 0, "diagnosis_clarification_count": 0,
            "validation_plan_id": None, "validation_execution_id": None,
            "confidence": None, "status": "diagnosis_offered",
            "created_at": now, "updated_at": now,
        }
        if cycle_number > int(job.get("max_repair_cycles") or MAX_REPAIR_CYCLES):
            cycle["status"] = "repair_limit_reached"
        cycle = repository.store_project_repair_cycle(cycle)
        return evidence, cycle

    def _run_repair_diagnosis(job: dict, access: dict, message: str, event_sink=None) -> ChatRunResponse:
        with repair_lock:
            current = repository.get_project_job(str(job["job_id"]))
            repair = dict(current.get("repair") or {})
            if repair.get("status") not in {"offered", "needs_clarification"}:
                return build_job_chat_run(
                    current, message=message,
                    response="This repair request was already handled or is no longer current.",
                    run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
                )
            try:
                evidence = repository.get_project_failure_evidence(str(repair["failure_evidence_id"]))
                cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                parent_patch, _snapshot = repository.get_project_patch(str(repair["parent_patch_id"]))
            except LookupError as error:
                raise HTTPException(status_code=409, detail="The persisted repair chain is incomplete.") from error
            if cycle.get("status") == "repair_limit_reached" or int(cycle.get("cycle_number") or 0) > int(current.get("max_repair_cycles") or MAX_REPAIR_CYCLES):
                updated_repair = {**repair, "status": "limit_reached", "warnings": ["The bounded repair-cycle limit was reached."]}
                updated = {**current, "repair": updated_repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(updated)
                _sync_project_job_action(updated)
                audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                            job_id=current["job_id"], patch_id=parent_patch["patch_id"], operation="repair_cycle_limit_reached",
                            status="blocked", metadata={"repair_cycle_id": cycle["repair_cycle_id"], "cycle_number": cycle["cycle_number"]})
                return build_job_chat_run(updated, message=message, response="The bounded repair-cycle limit was reached. No diagnosis, patch, or command was started.",
                                          run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
            current_state = project_state_hash(access["approved_root"])
            if current_state != evidence.get("project_state_hash"):
                evidence = {**evidence, "status": "stale"}
                repository.update_project_failure_evidence(evidence)
                cycle = {**cycle, "status": "stale_failure", "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_repair_cycle(cycle)
                updated_repair = {**repair, "status": "stale", "warnings": ["The project changed after this failure was recorded."]}
                updated = {**current, "repair": updated_repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(updated)
                _sync_project_job_action(updated)
                audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                            job_id=current["job_id"], patch_id=parent_patch["patch_id"], operation="repair_proposal_became_stale",
                            status="stale", metadata={"repair_cycle_id": cycle["repair_cycle_id"], "failure_evidence_id": evidence["evidence_id"]})
                return build_job_chat_run(updated, message=message,
                                          response="The project changed after this failure was recorded, so the old diagnosis cannot be used.",
                                          run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], patch_id=parent_patch["patch_id"], operation="diagnosis_requested",
                        status="running", metadata={"repair_cycle_id": cycle["repair_cycle_id"], "failure_evidence_id": evidence["evidence_id"]})
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], operation="fresh_repair_analysis_started", status="running",
                        metadata={"repair_cycle_id": cycle["repair_cycle_id"], "cycle_number": cycle["cycle_number"]})
            if event_sink is not None:
                progress_job = {**current, "repair": {**repair, "status": "diagnosing"}}
                progress = build_job_chat_run(
                    progress_job, message=message, response="Astra is running a fresh bounded structural analysis before diagnosis.",
                    run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
                )
                event_sink({"event": "project_diagnosis_started", "data": {"run": progress.model_dump(mode="json"), "job": progress_job}})
            try:
                index = build_project_index(
                    access["approved_root"], conversation_id=current["conversation_id"],
                    folder_access_id=access["action_id"], job_id=current["job_id"], previous=None,
                )
                relevant = list(dict.fromkeys([*evidence.get("referenced_files", []), *parent_patch.get("file_set", [])]))
                analysis = build_analysis_plan(index, str(current.get("user_task") or ""), relevant_paths=relevant)
            except (ProjectAnalysisError, ProjectSafetyError, OSError) as error:
                raise HTTPException(status_code=409, detail=_controlled_project_error(error)) from error
            repository.store_project_analysis(index)
            working = {**current, "analysis_id": index["analysis_id"], "analysis_index": index, "analysis": analysis,
                       "updated_at": datetime.now(timezone.utc).isoformat()}
            repository.update_project_job(working)
            cycle = {**cycle, "analysis_id": index["analysis_id"], "status": "diagnosing", "updated_at": datetime.now(timezone.utc).isoformat()}
            repository.update_project_repair_cycle(cycle)
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], operation="fresh_repair_analysis_completed", status="completed",
                        metadata={"repair_cycle_id": cycle["repair_cycle_id"], "analysis_id": index["analysis_id"], "file_count": len(index.get("files") or [])})

            def persist_diagnosis_started(value: dict) -> None:
                repository.store_project_diagnosis(value)
                audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                            job_id=current["job_id"], operation="model_diagnosis_requested", status="running",
                            metadata={"repair_cycle_id": cycle["repair_cycle_id"], "diagnosis_id": value["diagnosis_id"],
                                      "provider": value.get("provider"), "model": value.get("model")})
            failure_model = ProjectFailureEvidence.model_validate(evidence)
            deterministic_findings = deterministic_diagnosis(failure_model, parent_patch, index)
            if not deterministic_findings and int(cycle.get("diagnosis_model_calls") or 0) >= MAX_DIAGNOSIS_MODEL_CALLS:
                cycle = {**cycle, "status": "plan_only", "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_repair_cycle(cycle)
                updated_repair = {**repair, "status": "plan_only",
                                  "warnings": ["The bounded diagnosis model-call limit was reached."]}
                updated = {**working, "repair": updated_repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(updated)
                _sync_project_job_action(updated)
                audit_event(
                    repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                    job_id=current["job_id"], operation="diagnosis_model_call_limit_reached", status="blocked",
                    metadata={"repair_cycle_id": cycle["repair_cycle_id"], "model_calls": MAX_DIAGNOSIS_MODEL_CALLS},
                )
                return build_job_chat_run(
                    updated, message=message,
                    response="The bounded diagnosis model-call limit was reached. No repair preview or command was started.",
                    run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
                )
            try:
                diagnosis_result = diagnose_project_failure(
                    access["approved_root"], job=working,
                    failure=failure_model, parent_patch=parent_patch,
                    repair_cycle_number=int(cycle["cycle_number"]), gateway=diagnosis_gateway,
                    diagnosis_sink=persist_diagnosis_started,
                )
            except DiagnosisError as error:
                repository.store_project_diagnosis(error.diagnosis)
                cycle = {**cycle, "diagnosis_id": error.diagnosis["diagnosis_id"],
                         "diagnosis_model_calls": int(cycle.get("diagnosis_model_calls") or 0) + (1 if error.diagnosis.get("strategy") == "model_assisted" else 0),
                         "status": "needs_clarification" if error.code == "needs_clarification" else "plan_only",
                         "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_repair_cycle(cycle)
                clarification = error.diagnosis.get("clarification")
                updated_repair = {**repair, "status": cycle["status"], "diagnosis_id": error.diagnosis["diagnosis_id"],
                                  "diagnosis_strategy": error.diagnosis.get("strategy"), "provider": error.diagnosis.get("provider"),
                                  "model": error.diagnosis.get("model"), "confidence": error.diagnosis.get("confidence"),
                                  "warnings": error.diagnosis.get("uncertainty_codes") or [str(error)], "clarification": clarification}
                updated = {**working, "repair": updated_repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(updated)
                _sync_project_job_action(updated)
                failure_operation = {
                    "needs_clarification": "diagnosis_clarification_requested",
                    "provider_unavailable": "model_unavailable",
                    "timeout": "model_diagnosis_timed_out",
                    "malformed_or_unsafe": "diagnosis_response_rejected",
                }.get(error.code, "diagnosis_plan_only")
                audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                            job_id=current["job_id"], operation=failure_operation,
                            status="blocked", metadata={"repair_cycle_id": cycle["repair_cycle_id"], "diagnosis_id": error.diagnosis["diagnosis_id"], "reason_code": error.code})
                if event_sink is not None:
                    blocked_run = build_job_chat_run(updated, message=message, response=str(error), run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
                    event_sink({"event": "project_diagnosis_clarification" if error.code == "needs_clarification" else "project_repair_blocked",
                                "data": {"run": blocked_run.model_dump(mode="json"), "job": updated}})
                return build_job_chat_run(updated, message=message, response=str(error), run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
            diagnosis = diagnosis_result["diagnosis"]
            repository.store_project_diagnosis(diagnosis)
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], operation="deterministic_diagnosis_completed" if not diagnosis_result["model_used"] else "diagnosis_contract_validated",
                        status="completed", metadata={"repair_cycle_id": cycle["repair_cycle_id"], "diagnosis_id": diagnosis["diagnosis_id"],
                                                      "strategy": diagnosis["strategy"], "root_cause_count": len(diagnosis.get("root_causes") or [])})
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], operation="diagnosis_confidence_evaluated", status="completed",
                        metadata={"repair_cycle_id": cycle["repair_cycle_id"], "diagnosis_id": diagnosis["diagnosis_id"],
                                  "confidence": (diagnosis.get("confidence") or {}).get("level")})
            if event_sink is not None:
                diagnosed_job = {**working, "repair": {**repair, "status": "diagnosis_completed", "diagnosis_id": diagnosis["diagnosis_id"],
                                 "diagnosis_strategy": diagnosis["strategy"], "provider": diagnosis.get("provider"),
                                 "model": diagnosis.get("model"), "confidence": diagnosis.get("confidence")}}
                diagnosed_run = build_job_chat_run(
                    diagnosed_job, message=message, response="The bounded diagnosis completed; Astra is preparing a safe repair preview.",
                    run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
                )
                event_sink({"event": "project_diagnosis_completed", "data": {"run": diagnosed_run.model_dump(mode="json"), "job": diagnosed_job}})
            repair_context = {
                "diagnosis_id": diagnosis["diagnosis_id"], "failure_evidence_id": evidence["evidence_id"],
                "command_execution_id": evidence["command_execution_id"], "parent_patch_id": parent_patch["patch_id"],
                "repair_chain_id": cycle["repair_chain_id"], "repair_cycle_id": cycle["repair_cycle_id"],
                "cycle_number": cycle["cycle_number"],
            }
            repair_job = {
                **working, "status": "blocked", "revision_count": max(0, int(cycle["cycle_number"]) - 1),
                "user_task": diagnosis_result["repair_requirement"], "objective": diagnosis_result["repair_requirement"],
                "repair_context": repair_context,
            }
            def persist_synthesis_started(value: dict) -> None:
                repository.store_project_synthesis_attempt(value)
            try:
                bundle = prepare_job_patch_bundle(
                    access["approved_root"], repair_job,
                    model_gateway=(
                        None if current.get("delivery_job_id") else synthesis_gateway
                    ),
                    model_attempt_sink=persist_synthesis_started,
                )
                chain_context = {
                    **repair_context, "diagnosis_strategy": diagnosis["strategy"],
                    "provider": diagnosis.get("provider"), "model": diagnosis.get("model"),
                    "confidence": diagnosis.get("confidence"),
                    "root_cause_summary": str((diagnosis.get("root_causes") or [{}])[0].get("explanation") or "Bounded failure diagnosis")[:1000],
                    "affected_files": list((diagnosis.get("root_causes") or [{}])[0].get("affected_files") or [])[:20],
                }
                proposal = create_patch_proposal(
                    root=access["approved_root"], conversation_id=current["conversation_id"],
                    folder_access_id=access["action_id"], user_request=diagnosis_result["repair_requirement"],
                    changes=bundle["changes"], files_inspected=[item["path"] for item in bundle["changes"] if item["operation"] != "create"],
                    validation_plan=[str(item.get("purpose") or item.get("action")) for item in current.get("validation_plan") or []],
                    job_id=current["job_id"], analysis_context=bundle.get("analysis_context"), patch_chain_context=chain_context,
                )
                if current.get("delivery_job_id"):
                    delivery = repository.get_project_delivery_job(str(current["delivery_job_id"]))
                    proposal["delivery_job_id"] = delivery["delivery_job_id"]
                    proposal["work_unit_id"] = delivery.get("active_work_unit_id")
                    linked_delivery = link_patch_preview(delivery, patch=proposal)
                    if linked_delivery.get("status") == DeliveryStatus.REPLANNING.value:
                        _save_delivery_transition(delivery, linked_delivery, "repair_scope_change", "replanning_required")
                        raise ProjectDeliveryError("The repair preview exceeded the approved Stage 9 work-unit scope.", code="scope_change")
                    delivery_stored = _save_delivery_transition(
                        delivery, linked_delivery, "stage8_repair_preview", "awaiting_approval",
                        {"patch_id": proposal["patch_id"], "repair_cycle_id": cycle["repair_cycle_id"]},
                    )
                    _sync_delivery_action(delivery_stored)
            except (ProjectDeliveryError, ProjectJobError, ModelSynthesisError, ProjectAnalysisError, ProjectPatchError, ProjectSafetyError, OSError) as error:
                cycle = {**cycle, "diagnosis_id": diagnosis["diagnosis_id"], "status": "repair_rejected", "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_repair_cycle(cycle)
                updated_repair = {**repair, "status": "repair_rejected", "diagnosis_id": diagnosis["diagnosis_id"],
                                  "diagnosis_strategy": diagnosis["strategy"], "confidence": diagnosis.get("confidence"), "warnings": [_controlled_project_error(error)]}
                updated = {**working, "repair": updated_repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(updated)
                _sync_project_job_action(updated)
                audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                            job_id=current["job_id"], operation="repair_preview_rejected", status="blocked",
                            metadata={"repair_cycle_id": cycle["repair_cycle_id"], "diagnosis_id": diagnosis["diagnosis_id"], "reason": _controlled_project_error(error)})
                return build_job_chat_run(updated, message=message, response=_controlled_project_error(error), run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
            repository.store_project_patch(proposal)
            synthesis_attempt = bundle.get("synthesis_attempt")
            if synthesis_attempt:
                synthesis_attempt = {**synthesis_attempt, "status": "patch_proposed", "patch_id": proposal["patch_id"]}
                repository.store_project_synthesis_attempt(synthesis_attempt)
            cycle = {**cycle, "diagnosis_id": diagnosis["diagnosis_id"], "repair_patch_id": proposal["patch_id"],
                     "synthesis_attempt_id": synthesis_attempt.get("attempt_id") if synthesis_attempt else None,
                     "diagnosis_model_calls": int(cycle.get("diagnosis_model_calls") or 0) + (1 if diagnosis_result["model_used"] else 0),
                     "confidence": diagnosis.get("confidence"), "status": "preview_ready", "updated_at": datetime.now(timezone.utc).isoformat()}
            repository.update_project_repair_cycle(cycle)
            root_cause = (diagnosis.get("root_causes") or [{}])[0]
            updated_repair = {
                **repair, "status": "preview_ready", "repair_chain_id": cycle["repair_chain_id"],
                "repair_cycle_id": cycle["repair_cycle_id"], "cycle_number": cycle["cycle_number"],
                "failure_evidence_id": evidence["evidence_id"], "diagnosis_id": diagnosis["diagnosis_id"],
                "parent_patch_id": parent_patch["patch_id"], "repair_patch_id": proposal["patch_id"],
                "command_execution_id": evidence["command_execution_id"], "diagnosis_strategy": diagnosis["strategy"],
                "provider": diagnosis.get("provider"), "model": diagnosis.get("model"), "confidence": diagnosis.get("confidence"),
                "root_causes": diagnosis.get("root_causes") or [], "affected_files": root_cause.get("affected_files") or [],
                "affected_symbols": root_cause.get("affected_symbols") or [], "assumptions": diagnosis.get("assumptions") or [],
                "warnings": diagnosis.get("uncertainty_codes") or [], "clarification": None,
                "validation_rerun_status": "not_planned", "rollback_available": False,
            }
            analysis["prevalidation"] = bundle.get("prevalidation") or {"status": "passed", "checks": [], "warnings": []}
            updated = {**working, "status": "patch_proposed", "analysis": analysis, "repair": updated_repair,
                       "patch_ids": [*working.get("patch_ids", []), proposal["patch_id"]],
                       "updated_at": datetime.now(timezone.utc).isoformat()}
            if not repository.transition_project_job(updated, expected_statuses={"blocked"}):
                raise HTTPException(status_code=409, detail="The repair preview was already prepared or changed concurrently.")
            _sync_project_job_action(updated)
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], patch_id=proposal["patch_id"], operation="deterministic_repair_selected" if not synthesis_attempt else "model_assisted_repair_selected",
                        status="completed", metadata={"repair_cycle_id": cycle["repair_cycle_id"], "diagnosis_id": diagnosis["diagnosis_id"],
                                                      "synthesis_attempt_id": cycle.get("synthesis_attempt_id")})
            audit_event(repository, conversation_id=current["conversation_id"], folder_access_id=access["action_id"],
                        job_id=current["job_id"], patch_id=proposal["patch_id"], operation="repair_preview_created", status="proposed",
                        metadata={"repair_cycle_id": cycle["repair_cycle_id"], "cycle_number": cycle["cycle_number"], "relative_paths": proposal["file_set"]})
            return _project_patch_run(proposal)

    def _project_job_intercept(message: str, conversation_id: str, access: dict, event_sink=None) -> ChatRunResponse | None:
        active = repository.latest_active_project_job(conversation_id)
        if active is not None and active.get("status") == "blocked":
            repair = dict(active.get("repair") or {})
            if repair.get("status") == "offered" and detect_repair_request(message):
                return _run_repair_diagnosis(active, access, message, event_sink=event_sink)
            if repair.get("status") == "needs_clarification" and not detect_project_task(message):
                try:
                    cycle = repository.get_project_repair_cycle(str(repair["repair_cycle_id"]))
                    evidence = repository.get_project_failure_evidence(str(repair["failure_evidence_id"]))
                except LookupError as error:
                    raise HTTPException(status_code=409, detail="The repair clarification state is incomplete.") from error
                count = int(cycle.get("diagnosis_clarification_count") or 0) + 1
                if count > 2 or project_state_hash(access["approved_root"]) != evidence.get("project_state_hash"):
                    repair = {**repair, "status": "stale" if count <= 2 else "limit_reached",
                              "warnings": ["The diagnosis clarification could not continue safely."]}
                    updated = {**active, "repair": repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                    repository.update_project_job(updated)
                    _sync_project_job_action(updated)
                    return build_job_chat_run(updated, message=message, response="The bounded diagnosis clarification could not continue; no patch or command was started.",
                                              run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat())
                cycle = {**cycle, "diagnosis_clarification_count": count, "status": "diagnosis_offered",
                         "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_repair_cycle(cycle)
                clarification = {**dict(repair.get("clarification") or {}), "answer": " ".join(message.split())[:1000],
                                 "answered_at": datetime.now(timezone.utc).isoformat()}
                repair = {**repair, "status": "offered", "clarification": clarification}
                updated = {**active, "repair": repair, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(updated)
                audit_event(repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
                            job_id=active["job_id"], operation="diagnosis_clarification_answered", status="completed",
                            metadata={"repair_cycle_id": cycle["repair_cycle_id"], "clarification_count": count})
                return _run_repair_diagnosis(updated, access, message, event_sink=event_sink)
        if active is not None and active.get("status") == "needs_clarification" and not detect_project_task(message):
            job, _validated_access = _validated_project_job(str(active["job_id"]), conversation_id=conversation_id)
            try:
                updated = answer_clarification(job, message)
            except ProjectJobError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if not repository.transition_project_job(updated, expected_statuses={"needs_clarification"}):
                raise HTTPException(status_code=409, detail="The clarification was already answered or replayed.")
            _sync_project_job_action(updated)
            audit_event(
                repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
                job_id=updated["job_id"], operation="clarification_answered", status="completed",
                metadata={"answer_recorded": True},
            )
            audit_event(
                repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
                job_id=updated["job_id"], operation="plan_creation", status="completed",
                metadata={"relative_paths": updated["relevant_paths"], "step_count": len(updated["implementation_plan"]["steps"])},
            )
            return build_job_chat_run(
                updated, message=message,
                response="Clarification recorded. The evidence-backed plan is ready; no files were modified.",
                run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
            )
        if detect_project_job_followup(message) and active is not None:
            job, _validated_access = _validated_project_job(str(active["job_id"]), conversation_id=conversation_id)
            if (
                job.get("status") == "blocked"
                and int(job.get("revision_count") or 0) >= int(job.get("max_revision_cycles") or 3)
                and message.lower().strip().startswith("continue working")
            ):
                job = {**job, "max_revision_cycles": int(job.get("max_revision_cycles") or 3) + 1, "updated_at": datetime.now(timezone.utc).isoformat()}
                repository.update_project_job(job)
                _sync_project_job_action(job)
            return build_job_chat_run(
                job, message=message,
                response=f"This project job is {str(job['status']).replace('_', ' ')}. Review the current card for the next approval-gated step.",
                run_id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
            )
        if not detect_project_task(message):
            return None
        action_run_id = str(uuid4())
        job = create_project_job(
            root=access["approved_root"], conversation_id=conversation_id,
            folder_access_id=access["action_id"], user_task=message,
            action_run_id=action_run_id,
        )
        repository.store_project_job(job)
        repository.store_project_analysis(job["analysis_index"])
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="structure_analysis_started", status="running",
            metadata={"analysis_id": job["analysis_id"], "bounded_reader_reused": True},
        )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="job_created", status=job["status"],
            metadata={"relative_paths": job["relevant_paths"]},
        )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="requirement_extraction", status="completed",
            metadata={"relative_paths": job["relevant_paths"], "requirement_count": len(job["requirement_summaries"])},
        )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="structure_analysis_completed", status="completed",
            metadata=analysis_audit_metadata(job["analysis_index"], include_relationships=True),
        )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="symbol_index_creation", status="completed",
            metadata={"analysis_id": job["analysis_id"], "symbol_count": sum(len(item.get("symbols") or []) for item in job["analysis_index"]["files"])},
        )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="dependency_graph_creation", status="completed",
            metadata={"analysis_id": job["analysis_id"], "relationship_count": len(job["analysis_index"]["relationships"])},
        )
        for failed_path in job["analysis_index"].get("incremental", {}).get("parse_failures", [])[:20]:
            audit_event(
                repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
                job_id=job["job_id"], operation="project_parse_failure", status="partial",
                metadata={"analysis_id": job["analysis_id"], "relative_path": failed_path},
            )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="impact_analysis", status="completed",
            metadata={"relative_paths": [item["relative_path"] for item in job["analysis"]["coherent_file_set"]], "confidence": job["analysis"]["confidence"]["level"], "plan_only": job["analysis"]["plan_only"]},
        )
        audit_event(
            repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
            job_id=job["job_id"], operation="confidence_decision", status="plan_only" if job["analysis"]["plan_only"] else "eligible",
            metadata={"analysis_id": job["analysis_id"], "confidence": job["analysis"]["confidence"]["level"], "warning_count": len(job["analysis"]["uncertainties"])},
        )
        if job["status"] == "needs_clarification":
            audit_event(
                repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
                job_id=job["job_id"], operation="clarification_requested", status="pending",
                metadata={"missing_information_count": len(job["missing_information"])},
            )
        else:
            audit_event(
                repository, conversation_id=conversation_id, folder_access_id=access["action_id"],
                job_id=job["job_id"], operation="plan_creation", status="completed",
                metadata={"relative_paths": job["relevant_paths"], "step_count": len(job["implementation_plan"]["steps"])},
            )
        return build_job_chat_run(job, message=message)

    def _get_project_patch(patch_id: str) -> tuple[dict, list[dict]]:
        try:
            return repository.get_project_patch(patch_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Project patch not found.") from error

    def _project_patch_run(proposal: dict) -> ChatRunResponse:
        public = public_patch_proposal(proposal)
        files = ", ".join(proposal["file_set"])
        repair_context = dict(proposal.get("patch_chain_context") or {})
        is_repair = bool(repair_context)
        return ChatRunResponse(
            run_id=str(uuid4()), conversation_id=proposal["conversation_id"],
            user_message=proposal["user_request"],
            assistant_response=(
                f"I prepared an immutable {'repair ' if is_repair else ''}patch preview for {files}. Nothing has been changed yet. "
                f"Approve only this exact patch with APPROVE PATCH {proposal['patch_id']}."
            ),
            selected_specialist="project_workspace", intent="project_patch",
            confidence=1.0, rag_used=False,
            rag_skip_reason="Patch evidence came only from the approved project folder.", rag_context_count=0,
            source_count=len(proposal.get("files_inspected") or []),
            source_paths=list(proposal.get("files_inspected") or []),
            grounding_status="grounded" if proposal.get("files_inspected") else "weak",
            runtime_decision="patch_approval_required", safety_decision="approval_required",
            used_real_slm=False, slm_provider="not_invoked",
            slm_fallback_reason="Immutable patch proposal created deterministically.",
            memory_used=True, memory_summary=None, created_at=datetime.now(timezone.utc),
            trace_summary=[{
                "phase": "patch_proposal", "title": "Immutable patch preview created",
                "detail": f"Prepared {len(proposal['file_set'])} bounded file change(s); no writes occurred.",
                "status": "passed", "data": {"patch_id": proposal["patch_id"], "relative_paths": proposal["file_set"]},
            }],
            action={
                "action_id": proposal["patch_id"], "action_type": "project_patch",
                "title": "Review repair patch" if is_repair else "Review project patch",
                "summary": "The repair is ready for review. It has not been applied." if is_repair else "Review the exact bounded diff before changing files.",
                "steps": ["Review each proposed file change", "Approve the immutable patch", "Apply atomically with rollback snapshot", "Approve validation separately"],
                "safety_information": {"folder_access_is_not_patch_approval": True, "commands_will_not_run": True, "exact_confirmation": f"APPROVE PATCH {proposal['patch_id']}"},
                "status": "awaiting_approval", "approval_required": True,
                "result_summary": None, "error": None,
                "technical_details": {"project_patch": public, **({"project_repair": repair_context} if is_repair else {})},
            },
        )

    def _project_rollback_run(proposal: dict, message: str) -> ChatRunResponse:
        patch_id = str(proposal["patch_id"])
        return ChatRunResponse(
            run_id=str(uuid4()), conversation_id=proposal["conversation_id"], user_message=message,
            assistant_response=f"I prepared a rollback for patch {patch_id[:8]}. No files have been restored yet.",
            selected_specialist="project_workspace", intent="project_rollback", confidence=1.0,
            rag_used=False, rag_skip_reason="Rollback uses the bounded Astra snapshot.", rag_context_count=0,
            source_count=0, source_paths=[], grounding_status="none",
            runtime_decision="rollback_approval_required", safety_decision="approval_required",
            used_real_slm=False, slm_provider="not_invoked", slm_fallback_reason="Deterministic rollback proposal.",
            memory_used=True, memory_summary=None, created_at=datetime.now(timezone.utc),
            trace_summary=[{"phase": "rollback_proposal", "title": "Rollback approval required", "detail": "Current applied hashes will be verified before restoration.", "status": "passed"}],
            action={
                "action_id": f"rollback:{patch_id}", "action_type": "project_rollback",
                "title": "Rollback Astra patch", "summary": f"Restore {len(proposal['file_set'])} file(s) from the bounded snapshot.",
                "steps": ["Confirm current files still match Astra's applied hashes", "Restore the bounded snapshot atomically", "Record rollback audit status"],
                "safety_information": {"exact_confirmation": f"APPROVE ROLLBACK {patch_id}", "git_reset_used": False},
                "status": "awaiting_approval", "approval_required": True, "result_summary": None, "error": None,
                "technical_details": {"project_rollback": {"patch_id": patch_id, "relative_paths": proposal["file_set"], "status": "awaiting_approval"}},
            },
        )

    def _patch_application_summary(proposal: dict) -> str:
        created = sum(1 for item in proposal["changes"] if item["operation"] == "create")
        modified = sum(1 for item in proposal["changes"] if item["operation"] == "modify")
        deleted = sum(1 for item in proposal["changes"] if item["operation"] == "delete")
        return (
            f"Applied patch {proposal['patch_id'][:8]}: {modified} modified, {created} created, "
            f"{deleted} deleted; +{proposal['additions']}/-{proposal['deletions']}. "
            "Rollback is available. Tests have not run yet."
        )

    def _controlled_project_error(error: Exception) -> str:
        message = str(error).strip()
        allowed = (
            "approved", "project", "folder", "patch", "rollback", "file", "path", "symlink",
            "stale", "expired", "missing", "changed", "limit", "unsafe", "excluded",
            "command", "timeout", "allowlisted", "package", "shell", "job", "revision",
            "deterministic", "refine", "evidence", "model", "synthesis", "confidence",
            "clarification", "json", "contract", "provider", "diagnosis", "repair", "failure",
        )
        return message[:500] if message and any(term in message.lower() for term in allowed) else "The secure project operation could not be completed."

    def _public_failed_synthesis(error: ModelSynthesisError) -> dict:
        attempt = error.attempt
        confidence = attempt.get("confidence") or {"level": "low", "score": 0.0, "reasons": list(attempt.get("uncertainty_reasons") or []), "model_claim": None}
        return {
            "attempt_id": attempt.get("attempt_id"), "status": error.code,
            "strategy": "model_assisted", "contract_version": attempt.get("response_contract_version"),
            "provider": attempt.get("provider"), "model": attempt.get("model"),
            "evidence": attempt.get("evidence_summary") or {}, "confidence": confidence,
            "assumptions": list(attempt.get("assumptions") or []),
            "warnings": list(attempt.get("uncertainty_reasons") or [str(error)])[:20],
            "requires_clarification": error.code == "needs_clarification",
            "summary": str(error)[:500],
        }

    def _synthesis_audit_operation(code: str) -> str:
        return {
            "provider_unavailable": "model_provider_unavailable",
            "timeout": "model_synthesis_timeout",
            "needs_clarification": "model_synthesis_clarification_requested",
            "confidence_rejected": "model_synthesis_confidence_rejected",
            "malformed_or_unsafe": "model_synthesis_rejected",
            "clarification_limit": "model_synthesis_clarification_limit",
        }.get(code, "model_synthesis_failed")

    def _is_rollback_request(message: str) -> bool:
        normalized = " ".join(message.lower().strip().split())
        return (
            normalized.startswith("rollback patch ")
            or normalized in {
                "undo the last astra change", "undo the last astra change.",
                "restore the files from before that fix", "restore the files from before that fix.",
                "rollback the last patch", "rollback the last patch.",
            }
        )

    def _is_delivery_request(message: str) -> bool:
        normalized = " ".join(str(message or "").lower().split())
        explicit_delivery = bool(
            re.search(r"\b(?:deliver|delivery)\b", normalized)
            and re.search(r"\b(?:project|feature|change|fix|implementation|task)\b", normalized)
        )
        return explicit_delivery or detect_project_delivery_task(message)

    def _audit_project_run(run: ChatRunResponse, access: dict) -> None:
        metadata = {"relative_paths": run.source_paths, "file_count": run.source_count}
        for operation in ("folder_content_read", "project_search", "project_context_assembled"):
            audit_event(
                repository, conversation_id=run.conversation_id,
                folder_access_id=str(access["action_id"]), operation=operation,
                status="completed", metadata=metadata,
            )
        if run.intent == "project_plan":
            audit_event(
                repository, conversation_id=run.conversation_id,
                folder_access_id=str(access["action_id"]), operation="task_plan_generated",
                status="completed", metadata=metadata,
            )

    def _record_folder_failure(
        chat_run_id: str,
        action_id: str,
        folder_action: dict,
        error: str,
    ) -> None:
        repository.update_chat_run_action_for_id(
            chat_run_id,
            action_id,
            {
                "status": "failed",
                "approval_required": False,
                "result_summary": "Folder scan failed before any inventory was recorded.",
                "error": error,
                "technical_details": {
                    "folder_action": {
                        **folder_action,
                        "status": "failed",
                        "result_summary": "Folder scan failed before any inventory was recorded.",
                        "error": error,
                    }
                },
            },
        )

    def _folder_result_summary(scan: dict) -> str:
        summary = scan.get("summary") if isinstance(scan, dict) else {}
        if not isinstance(summary, dict):
            summary = {}
        readable = int(summary.get("readable") or 0)
        ignored = int(summary.get("ignored") or 0)
        total = int(summary.get("total_discovered") or 0)
        warnings = int(summary.get("warning_count") or 0)
        noun = "file" if readable == 1 else "files"
        warning_note = f" with {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
        return f"Scanned {readable} readable {noun} ({ignored} ignored, {total} discovered){warning_note}."

    def _run_assignment_copilot_request(
        request: AssignmentCopilotRunRequest,
    ):
        workspace_path = (
            _resolve_workspace_path(request.workspace_path)
            if request.workspace_path
            else None
        )
        document_path = _resolve_assignment_path(request.path) if request.path else None
        dataset_profile = (
            profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
            if request.dataset_path
            else None
        )
        return run_assignment_copilot(
            text=request.text,
            path=document_path,
            selected_assignment=request.selected_assignment,
            workspace_path=workspace_path,
            dataset_profile=dataset_profile,
            project_metadata=request.project_metadata,
            use_corpus=request.use_corpus,
            corpus_workspace_root=configured_workspace_root,
            generation_mode=request.generation_mode,
        )

    def _generate_assignment_workspace_from_payload(
        request: AssignmentWorkspaceGenerateRequest,
    ) -> dict:
        root = _resolve_workspace_write_path(request.workspace_path)
        copilot_mode = request.copilot_result.get("generation_mode")
        if copilot_mode is not None and copilot_mode != request.generation_mode:
            raise ValueError(
                "Requested generation_mode does not match the Copilot result."
            )
        raw_blueprints = request.copilot_result.get("grounded_file_blueprints", [])
        if not isinstance(raw_blueprints, list):
            raise ValueError("copilot_result grounded_file_blueprints must be a list.")
        blueprints = [
            GroundedFileBlueprint.model_validate(item)
            for item in raw_blueprints
            if isinstance(item, dict)
            and int(item.get("assignment_number", 0)) == request.assignment_number
        ]
        if not blueprints:
            raise ValueError(
                "No grounded file blueprints were provided for the selected assignment."
            )
        raw_summaries = request.copilot_result.get("corpus_grounding_summary", [])
        raw_plans = request.copilot_result.get("workspace_generation_plan", [])
        summary_index = (
            next(
                (
                    index
                    for index, item in enumerate(raw_plans)
                    if isinstance(item, dict)
                    and int(item.get("assignment_number", 0))
                    == request.assignment_number
                ),
                None,
            )
            if isinstance(raw_plans, list)
            else None
        )
        summary_payload = (
            raw_summaries[summary_index]
            if isinstance(raw_summaries, list)
            and summary_index is not None
            and len(raw_summaries) > summary_index
            else {}
        )
        summary = CorpusGroundingSummary.model_validate(summary_payload)
        result = write_grounded_workspace(
            root,
            blueprints,
            grounding_summary=summary,
            overwrite=request.overwrite,
        )
        return {
            **result.model_dump(mode="json"),
            "generation_mode": request.generation_mode,
        }

    def _assignment_chat_action(result: dict) -> dict:
        analysis = _assignment_analysis_summary(result)
        targets = _assignment_workspace_targets(result)
        action_id = str(uuid4())
        workspace_action = {
            "action_id": action_id,
            "status": "awaiting_approval" if targets else "completed",
            "targets": targets,
            "planned_file_count": sum(
                int(target.get("planned_file_count", 0)) for target in targets
            ),
            "results": [],
            "result_summary": None,
        }
        status = "awaiting_approval" if targets else "completed"
        return {
            "action_id": action_id,
            "action_type": "assignment",
            "title": analysis["title"],
            "summary": analysis["next_recommended_step"],
            "steps": [
                "Review the assignment analysis",
                "Approve workspace creation",
                "Write starter files without executing generated code",
            ],
            "safety_information": {
                "approval_required": bool(targets),
                "overwrite": False,
                "generated_code_executed": False,
            },
            "status": status,
            "approval_required": bool(targets),
            "result_summary": None,
            "technical_details": {
                "assignment_analysis": analysis,
                "workspace_action": workspace_action,
                "copilot_result": _sanitize_assignment_result(result),
            },
        }

    def _assignment_analysis_summary(result: dict) -> dict:
        parsed = result.get("parsed_document_summary")
        if not isinstance(parsed, dict):
            parsed = {}
        action_plan = result.get("action_plan")
        if not isinstance(action_plan, dict):
            action_plan = {}
        checklist = action_plan.get("checklist")
        evidence = result.get("evidence_checklist")
        if not isinstance(evidence, dict):
            evidence = {}
        evidence_summary = evidence.get("summary")
        if not isinstance(evidence_summary, dict):
            evidence_summary = {}
        report = result.get("report_draft")
        if not isinstance(report, dict):
            report = {}
        report_sections = report.get("sections")
        extracted = result.get("extracted_assignment_sections")
        return {
            "title": str(parsed.get("title") or "Assignment analysis"),
            "section_count": len(extracted) if isinstance(extracted, list) else 0,
            "task_count": len(checklist) if isinstance(checklist, list) else 0,
            "evidence_count": int(evidence_summary.get("total_required") or 0),
            "report_section_count": (
                len(report_sections) if isinstance(report_sections, list) else 0
            ),
            "next_recommended_step": str(result.get("next_recommended_step") or ""),
        }

    def _assignment_workspace_targets(result: dict) -> list[dict]:
        if result.get("generation_ready") is False:
            return []
        plans = result.get("workspace_generation_plan")
        if not isinstance(plans, list):
            return []
        targets: list[dict] = []
        seen: set[tuple[int, str]] = set()
        for item in plans:
            if not isinstance(item, dict):
                continue
            try:
                assignment_number = int(item.get("assignment_number"))
            except (TypeError, ValueError):
                continue
            workspace_path = str(item.get("workspace_path") or "").strip()
            if assignment_number not in {1, 2, 3} or not workspace_path:
                continue
            key = (assignment_number, workspace_path)
            if key in seen:
                continue
            seen.add(key)
            files = item.get("files")
            targets.append(
                {
                    "assignment_number": assignment_number,
                    "assignment_title": str(
                        item.get("assignment_title") or f"Assignment {assignment_number}"
                    ),
                    "workspace_path": workspace_path,
                    "generation_mode": str(
                        item.get("generation_mode")
                        or result.get("generation_mode")
                        or "mixed"
                    ),
                    "planned_file_count": len(files) if isinstance(files, list) else 0,
                }
            )
        return targets

    def _assignment_workspace_summary(results: list[dict]) -> str:
        created_count = sum(len(item.get("created_files") or []) for item in results)
        locations = [str(item.get("workspace_path")) for item in results]
        location = (
            locations[0]
            if len(locations) == 1
            else f"{len(locations)} assignment workspaces"
        )
        if created_count:
            suffix = "" if created_count == 1 else "s"
            return f"Created {created_count} starter file{suffix} in {location}."
        return (
            f"No new files were created in {location}. "
            "Existing or refused files are listed in the details."
        )

    def _sanitize_assignment_result(value):
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                if key in {"extracted_text", "raw_text", "file_bytes", "content_bytes"}:
                    continue
                if key == "source_path":
                    sanitized[key] = _safe_workspace_path_string(item)
                else:
                    sanitized[key] = _sanitize_assignment_result(item)
            return sanitized
        if isinstance(value, list):
            return [_sanitize_assignment_result(item) for item in value]
        if isinstance(value, str):
            return _safe_workspace_path_string(value)
        return value

    def _safe_workspace_path_string(value):
        if not isinstance(value, str) or not value:
            return value
        try:
            path = Path(value).expanduser()
            if path.is_absolute():
                resolved = path.resolve()
                return resolved.relative_to(configured_workspace_root).as_posix()
        except (OSError, ValueError):
            return value
        return value

    def _plan_chat_action(
        request: ChatRunRequest,
        detected: DetectedChatAction,
    ) -> ChatRunResponse:
        created_at = datetime.now(timezone.utc)
        conversation_id = request.conversation_id or str(uuid4())
        plan = plan_assignment_command(
            assignment_command_store,
            configured_workspace_root,
            _resolve_workspace_path("."),
            assignment_id="chat-action",
            assignment_task=detected.assignment_task,
            expected_result=detected.expected_result,
            action=detected.command_action,
            timeout_seconds=120,
        )
        return ChatRunResponse(
            run_id=str(uuid4()), conversation_id=conversation_id,
            user_message=request.message, assistant_response=detected.summary,
            selected_specialist="command_action", intent="command", confidence=1.0,
            rag_used=False, rag_skip_reason="Direct action intercepted before retrieval.",
            rag_context_count=0, runtime_decision="approval_required",
            safety_decision="approval_required", used_real_slm=False,
            slm_provider="not_invoked", slm_fallback_reason="Direct action did not require model generation.",
            memory_used=False, memory_summary=None, created_at=created_at,
            trace_summary=[{
                "phase": "action_interception", "title": "Direct action detected",
                "detail": "Created one allowlisted command plan before routing, retrieval, or generation.",
                "status": "passed",
            }],
            action={
                "action_type": detected.action_type, "title": detected.title,
                "summary": detected.summary,
                "steps": ["Approve the exact command", "Run once", "Report the result"],
                "safety_information": {
                    "approval_required": True, "destructive_actions_blocked": True,
                    "expected_file_modifications": "None expected from this command.",
                },
                "status": "awaiting_approval", "approval_required": True,
                "result_summary": None, "technical_details": {"command_plan": plan},
            },
        )

    def _plan_project_command_run(
        *,
        conversation_id: str,
        access: dict,
        message: str,
        action: str,
        expected_result: str,
        target: str | None = None,
        timeout_seconds: int = 120,
        job_id: str | None = None,
    ) -> ChatRunResponse:
        root = validate_root_identity(access["approved_root"], access["root_fingerprint"])
        plan = plan_assignment_command(
            assignment_command_store, root, root,
            assignment_id=f"project-job:{job_id}" if job_id else f"project:{access['action_id']}",
            assignment_task=message.strip(), expected_result=expected_result,
            action=action, target=target, timeout_seconds=timeout_seconds,
        )
        return ChatRunResponse(
            run_id=str(uuid4()), conversation_id=conversation_id, user_message=message,
            assistant_response="I prepared one bounded validation command for the approved project. It has not run yet.",
            selected_specialist="project_workspace", intent="project_command", confidence=1.0,
            rag_used=False, rag_skip_reason="Command planning does not use RAG.", rag_context_count=0,
            runtime_decision="command_approval_required", safety_decision="approval_required",
            used_real_slm=False, slm_provider="not_invoked", slm_fallback_reason="Structured allowlisted command plan.",
            memory_used=True, memory_summary=None, created_at=datetime.now(timezone.utc),
            trace_summary=[{
                "phase": "project_command_plan", "title": "Project validation command planned",
                "detail": "The working directory is the revalidated approved root; execution requires separate approval.",
                "status": "passed", "data": {"folder_access_id": access["action_id"], "plan_id": plan["plan_id"], "action": plan["action"]},
            }],
            action={
                "action_type": "project_command", "title": "Validate connected project",
                "summary": "Run one structured allowlisted command inside the approved project root.",
                "steps": ["Approve the exact command", "Run once with bounded output and timeout", "Review validation totals"],
                "safety_information": {"separate_command_approval": True, "shell_used": False, "working_directory": "approved project root", "package_installation_blocked": True},
                "status": "awaiting_approval", "approval_required": True, "result_summary": None,
                "technical_details": {"command_plan": plan, "project_scope": {"folder_access_id": access["action_id"], **({"job_id": job_id} if job_id else {})}},
            },
        )
    def _create_pending_chat_request(request: ChatRunRequest) -> ChatRequestRecord:
        if request.request_id is not None:
            raise HTTPException(status_code=400, detail={"code": "request_id_not_allowed", "message": "Request IDs are issued by the backend."})
        conversation_id = request.conversation_id or uuid4().hex
        if request.conversation_id and not repository.chat_conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail={"code": "conversation_not_found", "message": "Conversation not found."})
        request_id = uuid4().hex
        # The stored payload must reflect the resolved conversation_id, not
        # the possibly-None value the caller sent -- otherwise a later exact
        # request_fingerprint replay/conflict comparison would spuriously
        # mismatch every request that omitted conversation_id.
        resolved_request = request.model_copy(update={"conversation_id": conversation_id})
        return repository.create_chat_request(
            request_id=request_id,
            conversation_id=conversation_id,
            user_message=request.message,
            request_payload=resolved_request.model_dump(mode="json", exclude={"request_id"}),
            created_at=datetime.now(timezone.utc),
        )

    @application.post("/chat/requests", response_model=ChatRequestRecord)
    def chat_request_create(request: ChatRunRequest) -> ChatRequestRecord:
        return _create_pending_chat_request(request)

    @application.post("/chat/run", response_model=ChatRunResponse)
    def chat_run(request: ChatRunRequest) -> ChatRunResponse:
        folder_path = detect_folder_request(request.message)
        if folder_path is not None:
            run = create_folder_chat_run(
                message=request.message,
                requested_path=folder_path,
                conversation_id=request.conversation_id,
            )
            repository.store_chat_run(run)
            return run
        previous_turns = (
            repository.list_chat_runs_for_conversation(request.conversation_id)
            if request.conversation_id
            else []
        )
        access = completed_folder_access(previous_turns)
        if access is not None and _is_rollback_request(request.message):
            try:
                proposal, _snapshot = repository.latest_applied_project_patch(request.conversation_id or "")
            except LookupError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            run = _project_rollback_run(proposal, request.message)
            repository.store_chat_run(run)
            return run
        explicit_change = detect_explicit_patch_request(request.message) if access is not None else None
        if access is not None and explicit_change is not None:
            access = _completed_project_access(request.conversation_id or "")
            try:
                proposal = create_patch_proposal(
                    root=access["approved_root"], conversation_id=request.conversation_id or "",
                    folder_access_id=access["action_id"], user_request=request.message,
                    changes=[explicit_change], files_inspected=[explicit_change["path"]] if explicit_change["operation"] != "create" else [],
                )
            except (ProjectPatchError, ProjectSafetyError, FileNotFoundError, OSError) as error:
                raise HTTPException(status_code=400, detail=_controlled_project_error(error)) from error
            repository.store_project_patch(proposal)
            run = _project_patch_run(proposal)
            repository.store_chat_run(run)
            audit_event(repository, conversation_id=run.conversation_id, folder_access_id=access["action_id"], patch_id=proposal["patch_id"], operation="patch_proposed", status="proposed", metadata={"relative_paths": proposal["file_set"], "file_count": 1})
            return run
        active_engagement = repository.latest_active_client_engagement(request.conversation_id or "") if request.conversation_id else None
        if active_engagement is not None and active_engagement.get("state") == EngagementState.CLARIFICATION_REQUIRED.value and not detect_engagement_request(request.message):
            pending = [item for item in active_engagement.get("questions") or [] if item.get("status") == "pending"]
            if pending:
                use_assumptions = "reasonable assumption" in request.message.lower()
                supplied = {} if use_assumptions else {str(pending[0]["question_id"]): request.message}
                try:
                    engagement = engagement_service.submit_answers(
                        engagement_id=str(active_engagement["engagement_id"]),
                        expected_version=int(active_engagement["state_version"]), answers=supplied,
                        answered_by="local-user", use_reasonable_assumptions=use_assumptions,
                        idempotency_key=f"chat-answer:{uuid4().hex}",
                    )
                except EngagementError as error:
                    raise _engagement_http_error(error) from error
                run = build_engagement_chat_run(engagement, message=request.message)
                repository.store_chat_run(run)
                _sync_engagement_action(engagement)
                return run
        if active_engagement is not None and active_engagement.get("state") in {EngagementState.SCOPE_APPROVED.value, EngagementState.PROJECT_LAUNCHED.value} and _is_scope_change_message(request.message):
            try:
                engagement = engagement_service.request_scope_change(
                    engagement_id=str(active_engagement["engagement_id"]),
                    expected_version=int(active_engagement["state_version"]), requested_change=request.message,
                    requested_by="local-user", idempotency_key=f"chat-change:{uuid4().hex}",
                )
            except EngagementError as error:
                raise _engagement_http_error(error) from error
            run = build_engagement_chat_run(engagement, message=request.message)
            repository.store_chat_run(run)
            _sync_engagement_action(engagement)
            return run
        if detect_engagement_request(request.message):
            try:
                run = _start_client_engagement(request.message, request.conversation_id or uuid4().hex, _completed_project_access(request.conversation_id or "") if access is not None else None)
            except EngagementError as error:
                raise _engagement_http_error(error) from error
            repository.store_chat_run(run)
            return run
        if access is not None:
            secure_access = _completed_project_access(request.conversation_id or "")
            active_delivery = repository.latest_active_project_delivery_job(request.conversation_id or "")
            if active_delivery is not None and active_delivery.get("status") == DeliveryStatus.CLARIFICATION.value and not _is_delivery_request(request.message):
                try:
                    delivery_updated = submit_delivery_clarification(active_delivery, answer=request.message, root=secure_access["approved_root"])
                except ProjectDeliveryError as error:
                    raise _delivery_http_error(error) from error
                delivery_stored = _save_delivery_transition(active_delivery, delivery_updated, "clarification_response", "completed")
                delivery_run = build_delivery_chat_run(delivery_stored, message=request.message)
                repository.store_chat_run(delivery_run)
                _sync_delivery_action(delivery_stored)
                return delivery_run
            if _is_delivery_request(request.message):
                delivery_run = _start_project_delivery(
                    request.message, request.conversation_id or "", secure_access, persist_run=False,
                )
                repository.store_chat_run(delivery_run)
                return delivery_run
            job_run = _project_job_intercept(request.message, request.conversation_id or "", secure_access)
            if job_run is not None:
                repository.store_chat_run(job_run)
                return job_run
        project_intent = detect_project_intent(request.message)
        if access is not None and (project_intent is not None or is_folder_content_request(request.message)):
            access = _completed_project_access(request.conversation_id or "")
            run = create_project_chat_run(
                message=request.message,
                conversation_id=request.conversation_id or "",
                folder_access_id=access["action_id"],
                root=access["approved_root"],
            )
            repository.store_chat_run(run)
            _audit_project_run(run, access)
            return run
        detected = detect_chat_action(request.message)
        if detected is not None:
            run = (
                _plan_project_command_run(
                    conversation_id=request.conversation_id or "", access=access,
                    message=request.message, action=detected.command_action,
                    expected_result=detected.expected_result, timeout_seconds=120,
                )
                if access is not None
                else _plan_chat_action(request, detected)
            )
            repository.store_chat_run(run)
            return run
        # Not _create_pending_chat_request: unlike /chat/requests and
        # /chat/stream, /chat/run has always accepted (and auto-vivified) any
        # conversation_id without requiring it to already exist -- that
        # permissive fallback semantics must not change here.
        durable_conversation_id = request.conversation_id or uuid4().hex
        durable_request = repository.create_chat_request(
            request_id=uuid4().hex,
            conversation_id=durable_conversation_id,
            user_message=request.message,
            request_payload=request.model_copy(
                update={"conversation_id": durable_conversation_id}
            ).model_dump(mode="json", exclude={"request_id"}),
            created_at=datetime.now(timezone.utc),
        )
        request = request.model_copy(update={
            "conversation_id": durable_request.conversation_id,
            "request_id": durable_request.request_id,
        })
        durable_request = repository.claim_chat_request(durable_request.request_id)
        captured_lineage: list = []
        run = run_chat_workflow(
            request,
            workspace_root=configured_workspace_root,
            chat_runtime=chat_runtime_service,
            chat_request_id=durable_request.request_id,
            previous_turns=previous_turns,
            runtime_readiness=runtime_manager.readiness(),
            lineage_sink=captured_lineage.append,
        )
        repository.store_chat_run(run, request_id=durable_request.request_id)
        if captured_lineage:
            repository.record_chat_runtime_link(
                captured_lineage[0], project_run_id=request.project_run_id
            )
        _log_training_example(run)
        return run

    @application.post("/chat/stream")
    def chat_stream(request: ChatRunRequest) -> StreamingResponse:
        if request.request_id:
            try:
                durable_request = repository.get_chat_request(request.request_id)
            except LookupError as error:
                raise HTTPException(status_code=404, detail={"code": "request_not_found", "message": str(error)}) from error
            incoming_fingerprint = content_hash(request.model_dump(mode="json", exclude={"request_id"}))
            if incoming_fingerprint != durable_request.request_fingerprint:
                raise HTTPException(status_code=409, detail={"code": "request_binding_mismatch", "message": "The stream request does not match its persisted message, conversation, project, RAG preference, or safety settings."})
        else:
            durable_request = _create_pending_chat_request(request)
            request = request.model_copy(update={
                "conversation_id": durable_request.conversation_id,
                "request_id": durable_request.request_id,
            })
        if durable_request.status == "completed" and durable_request.run_id:
            completed_run = repository.get_chat_run(durable_request.run_id)

            def completed_event_stream():
                yield f"{json.dumps({'event': 'request_accepted', 'data': {'request': durable_request.model_dump(mode='json')}}, default=str)}\n"
                yield f"{json.dumps({'event': 'run_completed', 'data': {'run': completed_run.model_dump(mode='json'), 'trace_summary': completed_run.trace_summary}}, default=str)}\n"

            return StreamingResponse(completed_event_stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})
        if durable_request.status != "pending":
            raise HTTPException(status_code=409, detail={
                "code": "request_not_pending",
                "message": f"The persisted request is {durable_request.status} and cannot start another execution attempt.",
            })
        durable_request = repository.claim_chat_request(durable_request.request_id)
        folder_path = detect_folder_request(request.message)
        conversation_turns = (
            repository.list_chat_runs_for_conversation(request.conversation_id)
            if request.conversation_id and folder_path is None
            else []
        )
        access = completed_folder_access(conversation_turns) if folder_path is None else None
        active_job = repository.latest_active_project_job(request.conversation_id or "") if access is not None else None
        active_delivery = repository.latest_active_project_delivery_job(request.conversation_id or "") if access is not None else None
        active_engagement = repository.latest_active_client_engagement(request.conversation_id or "") if request.conversation_id else None
        engagement_request = bool(folder_path is None and detect_engagement_request(request.message))
        engagement_clarification = bool(
            active_engagement is not None
            and active_engagement.get("state") == EngagementState.CLARIFICATION_REQUIRED.value
            and not engagement_request
        )
        engagement_change = bool(
            active_engagement is not None
            and active_engagement.get("state") in {EngagementState.SCOPE_APPROVED.value, EngagementState.PROJECT_LAUNCHED.value}
            and _is_scope_change_message(request.message)
        )
        delivery_clarification = bool(
            active_delivery is not None
            and active_delivery.get("status") == DeliveryStatus.CLARIFICATION.value
            and not _is_delivery_request(request.message)
        )
        delivery_request = bool(access is not None and _is_delivery_request(request.message))
        job_request = bool(
            access is not None
            and not delivery_request
            and (
                detect_project_task(request.message)
                or detect_project_job_followup(request.message)
                or detect_repair_request(request.message)
                or (active_job is not None and active_job.get("status") == "needs_clarification")
            )
        )
        explicit_change = (
            detect_explicit_patch_request(request.message)
            if folder_path is None and access is not None
            else None
        )
        project_intent = detect_project_intent(request.message) if folder_path is None and not job_request else None
        project_request = bool(folder_path is None and access is not None and explicit_change is None and (project_intent is not None or is_folder_content_request(request.message)))
        patch_request = bool(folder_path is None and access is not None and explicit_change is not None)
        rollback_request = bool(folder_path is None and access is not None and _is_rollback_request(request.message))
        detected = detect_chat_action(request.message) if folder_path is None else None
        previous_turns = (
            conversation_turns
            if detected is None and folder_path is None and not project_request and not patch_request and not rollback_request and not job_request and not delivery_request and not delivery_clarification and not engagement_request and not engagement_clarification and not engagement_change
            else []
        )
        events: queue.Queue[dict | object] = queue.Queue()
        done = object()
        captured_lineage: list = []

        def worker() -> None:
            try:
                if folder_path is not None:
                    run = create_folder_chat_run(
                        message=request.message,
                        requested_path=folder_path,
                        conversation_id=request.conversation_id,
                    )
                elif rollback_request:
                    proposal, _snapshot = repository.latest_applied_project_patch(request.conversation_id or "")
                    run = _project_rollback_run(proposal, request.message)
                elif engagement_request:
                    secure_access = _completed_project_access(request.conversation_id or "") if access is not None else None
                    run = _start_client_engagement(request.message, request.conversation_id or uuid4().hex, secure_access)
                elif engagement_clarification:
                    if active_engagement is None:
                        raise RuntimeError("The pending engagement clarification disappeared.")
                    pending = [item for item in active_engagement.get("questions") or [] if item.get("status") == "pending"]
                    use_assumptions = "reasonable assumption" in request.message.lower()
                    supplied = {} if use_assumptions else ({str(pending[0]["question_id"]): request.message} if pending else {})
                    engagement = engagement_service.submit_answers(
                        engagement_id=str(active_engagement["engagement_id"]), expected_version=int(active_engagement["state_version"]),
                        answers=supplied, answered_by="local-user", use_reasonable_assumptions=use_assumptions,
                        idempotency_key=f"stream-answer:{uuid4().hex}",
                    )
                    run = build_engagement_chat_run(engagement, message=request.message)
                    _sync_engagement_action(engagement)
                elif engagement_change:
                    if active_engagement is None:
                        raise RuntimeError("The active engagement disappeared.")
                    engagement = engagement_service.request_scope_change(
                        engagement_id=str(active_engagement["engagement_id"]), expected_version=int(active_engagement["state_version"]),
                        requested_change=request.message, requested_by="local-user", idempotency_key=f"stream-change:{uuid4().hex}",
                    )
                    run = build_engagement_chat_run(engagement, message=request.message)
                    _sync_engagement_action(engagement)
                elif patch_request:
                    secure_access = _completed_project_access(request.conversation_id or "")
                    proposal = create_patch_proposal(
                        root=secure_access["approved_root"], conversation_id=request.conversation_id or "",
                        folder_access_id=secure_access["action_id"], user_request=request.message,
                        changes=[explicit_change], files_inspected=[explicit_change["path"]] if explicit_change["operation"] != "create" else [],
                    )
                    repository.store_project_patch(proposal)
                    run = _project_patch_run(proposal)
                elif delivery_request:
                    secure_access = _completed_project_access(request.conversation_id or "")
                    run = _start_project_delivery(
                        request.message, request.conversation_id or "", secure_access, persist_run=False,
                    )
                elif delivery_clarification:
                    secure_access = _completed_project_access(request.conversation_id or "")
                    if active_delivery is None:
                        raise RuntimeError("The pending delivery clarification disappeared.")
                    delivery_updated = submit_delivery_clarification(
                        active_delivery, answer=request.message, root=secure_access["approved_root"],
                    )
                    delivery_stored = _save_delivery_transition(
                        active_delivery, delivery_updated, "clarification_response", "completed",
                    )
                    run = build_delivery_chat_run(delivery_stored, message=request.message)
                    _sync_delivery_action(delivery_stored)
                elif job_request:
                    secure_access = _completed_project_access(request.conversation_id or "")
                    run = _project_job_intercept(request.message, request.conversation_id or "", secure_access, event_sink=events.put)
                    if run is None:
                        raise RuntimeError("Project job interception did not produce a run.")
                elif project_request:
                    secure_access = _completed_project_access(request.conversation_id or "")
                    run = create_project_chat_run(
                        message=request.message,
                        conversation_id=request.conversation_id or "",
                        folder_access_id=secure_access["action_id"],
                        root=secure_access["approved_root"],
                    )
                elif detected is not None:
                    run = (
                        _plan_project_command_run(
                            conversation_id=request.conversation_id or "", access=access,
                            message=request.message, action=detected.command_action,
                            expected_result=detected.expected_result, timeout_seconds=120,
                        )
                        if access is not None
                        else _plan_chat_action(request, detected)
                    )
                else:
                    run = run_chat_workflow(
                        request,
                        workspace_root=configured_workspace_root,
                        chat_runtime=chat_runtime_service,
                        chat_request_id=durable_request.request_id,
                        previous_turns=previous_turns,
                        event_sink=events.put,
                        runtime_readiness=runtime_manager.readiness(),
                        lineage_sink=captured_lineage.append,
                    )
                repository.store_chat_run(run, request_id=durable_request.request_id)
                if captured_lineage:
                    repository.record_chat_runtime_link(
                        captured_lineage[0], project_run_id=request.project_run_id
                    )
                if (engagement_request or engagement_clarification or engagement_change) and run.action is not None:
                    events.put({"event": "client_engagement_updated", "data": {"run": run.model_dump(mode="json"), "engagement": run.action.get("technical_details", {}).get("client_engagement", {})}})
                if (delivery_request or delivery_clarification) and run.action is not None:
                    events.put({"event": "project_delivery_updated", "data": {"run": run.model_dump(mode="json"), "delivery": run.action.get("technical_details", {}).get("project_delivery", {})}})
                if job_request and run.action is not None and run.action.get("action_type") == "project_job":
                    job_payload = run.action.get("technical_details", {}).get("project_job", {})
                    job_status = job_payload.get("status") if isinstance(job_payload, dict) else None
                    event_name = "project_job_created" if run.user_message == job_payload.get("user_task") else "project_job_updated"
                    event_data = {"run": run.model_dump(mode="json"), "job": job_payload}
                    events.put({"event": event_name, "data": event_data})
                    if job_status == "needs_clarification":
                        events.put({"event": "clarification_required", "data": event_data})
                    elif job_status == "planned":
                        events.put({"event": "project_plan_ready", "data": event_data})
                    if event_name == "project_job_created" and isinstance(job_payload.get("analysis"), dict):
                        events.put({"event": "project_analysis_completed", "data": event_data})
                        events.put({"event": "project_impact_ready", "data": event_data})
                    repair_status = (job_payload.get("repair") or {}).get("status") if isinstance(job_payload, dict) else None
                    repair_event = {
                        "offered": "project_diagnosis_offered",
                        "needs_clarification": "project_diagnosis_clarification",
                        "plan_only": "project_repair_blocked",
                        "stale": "project_repair_blocked",
                        "limit_reached": "project_repair_blocked",
                    }.get(repair_status)
                    if repair_event:
                        events.put({"event": repair_event, "data": event_data})
                if job_request and run.action is not None and run.action.get("action_type") == "project_patch":
                    repair_payload = run.action.get("technical_details", {}).get("project_repair")
                    if isinstance(repair_payload, dict):
                        events.put({"event": "project_repair_ready", "data": {"run": run.model_dump(mode="json"), "repair": repair_payload}})
                if project_request:
                    _audit_project_run(run, access)
                if patch_request:
                    audit_event(repository, conversation_id=run.conversation_id, folder_access_id=str(access["action_id"]), patch_id=str(proposal["patch_id"]), operation="patch_proposed", status="proposed", metadata={"relative_paths": proposal["file_set"], "file_count": 1})
                if run.action is None and not project_request and not patch_request and not rollback_request:
                    _log_training_example(run)
                if run.action is not None:
                    events.put(
                        {
                            "event": "action_required",
                            "data": {
                                "run": run.model_dump(mode="json"),
                                "action": run.action,
                            },
                        }
                    )
                events.put(
                    {
                        "event": "run_completed",
                        "data": {
                            "run": run.model_dump(mode="json"),
                            "trace_summary": run.trace_summary,
                        },
                    }
                )
            except Exception as error:
                try:
                    repository.update_chat_request_status(
                        durable_request.request_id,
                        status="failed",
                        error=str(error)[:1000],
                    )
                except (LookupError, ValueError):
                    pass
                events.put(
                    {
                        "event": "run_failed",
                        "data": {"error": str(error)},
                    }
                )
            finally:
                events.put(done)

        def event_stream():
            yield f"{json.dumps({'event': 'request_accepted', 'data': {'request': durable_request.model_dump(mode='json')}}, default=str)}\n"
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            while True:
                item = events.get()
                if item is done:
                    break
                yield f"{json.dumps(item, default=str)}\n"

        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    def _log_training_example(run: ChatRunResponse) -> None:
        try:
            log_chat_run_example(configured_workspace_root, run)
        except Exception:
            return

    @application.get("/training/dataset/status")
    def training_dataset_status() -> dict:
        return get_dataset_status(configured_workspace_root)

    @application.get("/training/examples")
    def training_examples(
        label_status: str | None = Query(default=None),
        final_label: str | None = Query(default=None),
        source: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        return list_examples(
            configured_workspace_root,
            label_status=label_status,
            final_label=final_label,
            source=source,
            limit=limit,
        )

    @application.post("/training/examples")
    def training_example_create(request: TrainingExampleCreateRequest) -> dict:
        return log_manual_example(configured_workspace_root, request)

    @application.post("/training/examples/{example_id}/label")
    def training_example_label(
        example_id: str,
        request: TrainingExampleLabelRequest,
    ) -> dict:
        try:
            return update_example_label(configured_workspace_root, example_id, request)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/training/export")
    def training_export(request: TrainingExportRequest) -> dict:
        return export_examples(
            configured_workspace_root,
            export_format=request.format,
        )

    @application.get("/chat/runs", response_model=ChatRunsResponse)
    def chat_runs(limit: int = Query(default=20, ge=1, le=100)) -> ChatRunsResponse:
        return ChatRunsResponse(items=repository.list_chat_runs(limit=limit))

    @application.get("/chat/conversations", response_model=ChatConversationsResponse)
    def chat_conversations(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> ChatConversationsResponse:
        return ChatConversationsResponse(
            items=repository.list_chat_conversations(limit=limit)
        )

    @application.post("/chat/conversations", response_model=ChatConversationDetail)
    def create_chat_conversation() -> ChatConversationDetail:
        return repository.create_chat_conversation(
            conversation_id=uuid4().hex,
            created_at=datetime.now(timezone.utc),
        )

    @application.get(
        "/chat/conversations/{conversation_id}",
        response_model=ChatConversationDetail,
    )
    def chat_conversation(conversation_id: str) -> ChatConversationDetail:
        try:
            detail = repository.get_chat_conversation(conversation_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        jobs = [
            public_project_job(job)
            for job in repository.list_project_jobs_for_conversation(conversation_id)
        ]
        deliveries = [
            _public_read_delivery(_read_project_delivery(
                str(job["delivery_job_id"]), conversation_id=conversation_id,
            )[0])
            for job in repository.list_project_delivery_jobs_for_conversation(conversation_id)
        ]
        projects = [
            build_canonical_project_response(
                canonical_project_service,
                item.project_run_id,
                coordinator=project_coordinator,
            ).model_dump(mode="json")
            for item in canonical_project_service.list_projects(conversation_id)
        ]
        return ChatConversationDetail.model_validate({
            **detail.model_dump(mode="json"),
            "project_jobs": jobs,
            "project_deliveries": deliveries,
            "projects": projects,
        })

    @application.delete(
        "/chat/conversations/{conversation_id}",
        response_model=ChatConversationDeleteResponse,
    )
    def delete_chat_conversation(conversation_id: str) -> ChatConversationDeleteResponse:
        deleted_turns = repository.delete_chat_conversation(conversation_id)
        return ChatConversationDeleteResponse(
            conversation_id=conversation_id,
            deleted=deleted_turns > 0,
            deleted_turns=deleted_turns,
        )

    def build_patch_proposal(
        *,
        code: str,
        code_hash: str,
        path: str,
        analysis_id: str,
        finding,
    ) -> PatchProposalResponse | None:
        if finding.validation.status != "passed" or finding.suggested_code is None:
            return None
        if finding.finding_id is None:
            return None

        original_lines = code.splitlines(keepends=True)
        replacement_lines = finding.suggested_code.splitlines(keepends=True)

        if len(original_lines) != len(replacement_lines):
            return None

        changed_lines = [
            line_number
            for line_number, (original, replacement) in enumerate(
                zip(original_lines, replacement_lines),
                start=1,
            )
            if original != replacement
        ]

        if len(changed_lines) != 1:
            return None

        line_number = changed_lines[0]

        return PatchProposalResponse(
            proposal_id=str(uuid4()),
            analysis_id=analysis_id,
            finding_id=finding.finding_id,
            path=path,
            original_file_sha256=code_hash,
            start_line=line_number,
            end_line=line_number,
            replacement=replacement_lines[line_number - 1],
            validation_status="passed",
        )

    def analyze_source(
        code: str,
        filename: str | None,
        *,
        propose_file_patches: bool = False,
    ) -> AnalyzeResponse:
        analysis_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        line_count = len(code.splitlines())

        result = add_validated_fixes(code, analyze_python_code(code))

        findings = [
            issue.model_copy(update={"finding_id": str(uuid4())})
            for issue in result.issues
        ]

        suggestions = list(dict.fromkeys(issue.suggestion for issue in findings))
        validated_fix_count = sum(
            issue.validation.status == "passed" for issue in findings
        )

        repository.store_analysis(
            analysis_id=analysis_id,
            created_at=created_at,
            code_hash=code_hash,
            language="python",
            filename=filename,
            code_length=len(code),
            line_count=line_count,
            issue_count=len(findings),
            parse_success=result.parse_success,
            validated_fix_count=validated_fix_count,
            phase=APP_PHASE,
            findings=findings,
        )

        patch_proposals = []

        if propose_file_patches and filename is not None:
            patch_proposals = [
                proposal
                for finding in findings
                if (
                    proposal := build_patch_proposal(
                        code=code,
                        code_hash=code_hash,
                        path=filename,
                        analysis_id=analysis_id,
                        finding=finding,
                    )
                )
                is not None
            ]
            repository.store_patch_proposals(patch_proposals)

        return AnalyzeResponse(
            analysis_id=analysis_id,
            success=True,
            language="python",
            filename=filename,
            issues=findings,
            suggestions=suggestions,
            patch_proposals=patch_proposals,
            metadata={
                "phase": APP_PHASE,
                "engine": "python-ast-static-analyzer",
                "suggestion_engine": "deterministic-validated-fixes",
                "parse_success": result.parse_success,
                "rules_triggered": [issue.rule_id for issue in findings],
                "validated_fix_count": validated_fix_count,
                "code_sha256": code_hash,
                "code_stored": False,
                "code_length": len(code),
                "line_count": line_count,
            },
            created_at=created_at,
        )

    @application.post("/analyze", response_model=AnalyzeResponse)
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        language = request.language.strip().lower()

        if language != "python":
            raise HTTPException(
                status_code=400,
                detail="Only Python source code is currently supported.",
            )

        return analyze_source(request.code, request.filename)

    @application.post("/analyze-file", response_model=AnalyzeResponse)
    def analyze_file(request: AnalyzeFileRequest) -> AnalyzeResponse:
        requested_path = Path(request.path)

        if requested_path.is_absolute():
            raise HTTPException(
                status_code=400,
                detail="File paths must be relative to the configured workspace root.",
            )

        resolved_path = (configured_workspace_root / requested_path).resolve()

        try:
            relative_path = resolved_path.relative_to(configured_workspace_root)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail="File path must stay within the configured workspace root.",
            ) from error

        if resolved_path.suffix.lower() != ".py":
            raise HTTPException(
                status_code=400,
                detail="Only Python files can currently be analyzed.",
            )

        if not resolved_path.exists():
            raise HTTPException(status_code=404, detail="Python file was not found.")

        if not resolved_path.is_file():
            raise HTTPException(status_code=400, detail="Requested path is not a file.")

        try:
            code = resolved_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(
                status_code=400,
                detail="Python file must be UTF-8 encoded.",
            ) from error

        return analyze_source(
            code,
            relative_path.as_posix(),
            propose_file_patches=True,
        )

    @application.post(
        "/analyze-project",
        response_model=JobAcceptedResponse,
        status_code=202,
    )
    def analyze_project(request: AnalyzeProjectRequest) -> JobAcceptedResponse:
        requested_path = Path(request.path)

        if requested_path.is_absolute():
            raise HTTPException(
                status_code=400,
                detail="Project paths must be relative to the configured workspace root.",
            )

        resolved_path = (configured_workspace_root / requested_path).resolve()

        try:
            relative_path = resolved_path.relative_to(configured_workspace_root)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail="Project path must stay within the configured workspace root.",
            ) from error

        if not resolved_path.exists():
            raise HTTPException(status_code=404, detail="Project directory was not found.")

        if not resolved_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Requested project path is not a directory.",
            )

        queued = job_queue.enqueue(
            "analyze_project",
            {"path": relative_path.as_posix()},
        )

        return JobAcceptedResponse(
            job_id=queued.job_id,
            status="queued",
            status_url=f"/jobs/{queued.job_id}",
        )

    @application.post(
        "/orchestrate",
        response_model=JobAcceptedResponse,
        status_code=202,
    )
    def orchestrate(request: OrchestrateRequest) -> JobAcceptedResponse:
        requested_path = Path(request.path)

        if requested_path.is_absolute():
            raise HTTPException(
                status_code=400,
                detail="Task paths must be relative to the configured workspace root.",
            )

        resolved_path = (configured_workspace_root / requested_path).resolve()

        try:
            relative_path = resolved_path.relative_to(configured_workspace_root)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail="Task path must stay within the configured workspace root.",
            ) from error

        if not resolved_path.exists():
            raise HTTPException(status_code=404, detail="Task directory was not found.")

        if not resolved_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Task path must be a directory.",
            )

        if request.allow_edits or request.allow_tests:
            raise HTTPException(
                status_code=503,
                detail={
                    "schema_version": "astra.legacy-execution-retired.v1",
                    "code": "legacy_host_execution_retired",
                    "message": (
                        "Orchestrator file edits and test execution run directly on the "
                        "host and have been retired pending canonical Docker-isolated "
                        "worker integration. No project code was executed on the host. "
                        "Retry with allow_edits=false and allow_tests=false for "
                        "read-only orchestration, or use the canonical project pipeline "
                        "for isolated execution."
                    ),
                },
            )

        queued = job_queue.enqueue(
            "orchestrate_task",
            {
                "goal": request.goal,
                "path": relative_path.as_posix(),
                "allow_edits": request.allow_edits,
                "allow_tests": request.allow_tests,
                "approval_mode": "never"
                if not request.allow_edits
                else request.approval_mode,
                "allow_dirty_worktree": request.allow_dirty_worktree,
                "rollback_on_test_failure": request.rollback_on_test_failure,
                "max_patch_changed_lines": request.max_patch_changed_lines,
                "allowed_patch_files": request.allowed_patch_files,
                "max_steps": request.max_steps,
                "proposer": request.proposer,
                "advisor_runtime_mode": request.advisor_runtime_mode,
                "slm_model": request.slm_model,
                "slm_base_url": request.slm_base_url,
            },
        )

        return JobAcceptedResponse(
            job_id=queued.job_id,
            status="queued",
            status_url=f"/jobs/{queued.job_id}",
        )

    @application.get("/rules", response_model=RulesResponse)
    def rules() -> RulesResponse:
        return RulesResponse(items=get_rule_metadata())

    @application.get("/tools", response_model=ToolsResponse)
    def tools() -> ToolsResponse:
        return ToolsResponse(items=get_tool_metadata())

    @application.get("/history", response_model=HistoryResponse)
    def history(limit: int = Query(default=20, ge=1, le=100)) -> HistoryResponse:
        return HistoryResponse(items=repository.list_analyses(limit=limit))

    @application.get("/metrics", response_model=MetricsResponse)
    def metrics() -> MetricsResponse:
        return repository.get_metrics(phase=APP_PHASE)

    @application.get("/jobs", response_model=JobsResponse)
    def jobs(
        limit: int = Query(default=20, ge=1, le=100),
        status: JobStatus | None = Query(default=None),
    ) -> JobsResponse:
        return JobsResponse(items=job_queue.list_jobs(limit=limit, status=status))

    @application.get("/jobs/{job_id}", response_model=JobResponse)
    def job(job_id: str) -> JobResponse:
        try:
            return job_queue.get(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @application.get("/jobs/{job_id}/trace/compact")
    def compact_job_trace(job_id: str) -> dict:
        try:
            job_item = job_queue.get(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        if hasattr(job_item, "model_dump"):
            job_data = job_item.model_dump()
        elif isinstance(job_item, dict):
            job_data = job_item
        else:
            job_data = dict(job_item)

        result = job_data.get("result") or {}
        trace = result.get("trace") or []

        compacted = compact_orchestrator_trace(trace)

        return {
            "job_id": job_id,
            "status": job_data.get("status"),
            "orchestrator_status": result.get("status"),
            "final_response": result.get("final_response"),
            "trace": compacted,
        }

    @application.post("/jobs/{job_id}/approve-patch")
    def approve_patch(job_id: str) -> dict:
        try:
            job_item = job_queue.get(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        result = job_item.result or {}
        task_id = str(result.get("task_id") or "")
        if not task_id and isinstance(result.get("trace"), dict):
            task_id = str(result["trace"].get("task_id") or "")
        if not task_id:
            raise HTTPException(
                status_code=400,
                detail="Job does not contain a pending orchestrator task id.",
            )

        raise HTTPException(
            status_code=503,
            detail={
                "schema_version": "astra.legacy-execution-retired.v1",
                "code": "legacy_host_execution_retired",
                "message": (
                    "Orchestrator patch approval applies changes and runs tests "
                    "directly on the host and has been retired pending canonical "
                    "Docker-isolated worker integration. No project code was executed "
                    "on the host."
                ),
            },
        )
        try:
            approval_result = approve_pending_patch(
                approval_root="data/app/pending_approvals",
                approval_id=task_id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PolicyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        return {
            "job_id": job_id,
            "approval_id": task_id,
            "status": "applied" if approval_result.get("applied") else "rolled_back",
            "result": approval_result,
        }

    @application.post("/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str) -> JobResponse:
        try:
            return job_queue.request_cancel(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @application.post("/feedback", response_model=FeedbackResponse)
    def feedback(request: FeedbackRequest) -> FeedbackResponse:
        try:
            return repository.store_feedback(
                feedback_id=str(uuid4()),
                analysis_id=request.analysis_id,
                finding_id=request.finding_id,
                helpful=request.helpful,
                suggestion_accepted=request.suggestion_accepted,
                created_at=datetime.now(timezone.utc),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/patch/apply", response_model=PatchApplyResponse)
    def patch_apply(request: PatchApplyRequest) -> PatchApplyResponse:
        raise HTTPException(
            status_code=503,
            detail={
                "schema_version": "astra.legacy-execution-retired.v1",
                "code": "legacy_host_execution_retired",
                "message": (
                    "Deterministic patch application writes directly to the host "
                    "workspace and has been retired pending canonical Docker-isolated "
                    "worker integration. No project code was executed on the host. The "
                    "validated patch proposal remains available for manual review."
                ),
            },
        )
        try:
            proposal = repository.get_patch_proposal(request.proposal_id)
            result = apply_patch_proposal(
                workspace_root=configured_workspace_root,
                proposal=proposal,
            )
            repository.record_patch_application(
                result,
                applied_at=datetime.now(timezone.utc),
            )

            if request.run_pytest:
                verification = run_pytest_verification(configured_workspace_root)
                repository.record_patch_verification(proposal.proposal_id, verification)
                result = result.model_copy(update={"verification": verification})

            return result

        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        except PatchApplyConflictError as error:
            try:
                repository.update_patch_proposal_status(
                    proposal_id=request.proposal_id,
                    status="conflict",
                )
            except LookupError:
                pass

            raise HTTPException(status_code=409, detail=str(error)) from error

        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @application.post("/patch/preview", response_model=PatchPreviewResponse)
    def patch_preview(request: PatchPreviewRequest) -> PatchPreviewResponse:
        try:
            proposal = repository.get_patch_proposal(request.proposal_id)
            return preview_patch_proposal(
                workspace_root=configured_workspace_root,
                proposal=proposal,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    def _canonical_folder_authority(conversation_id: str) -> dict[str, str]:
        access = _completed_project_access(conversation_id)
        return {
            "status": "completed",
            "action_id": str(access["action_id"]),
            "conversation_id": conversation_id,
            "workspace_id": str(access["action_id"]),
            "repository_root_fingerprint": str(access["root_fingerprint"]),
            "repository_root": str(access["approved_root"]),
        }

    application.include_router(
        create_project_router(
            canonical_project_service,
            folder_authority_resolver=_canonical_folder_authority,
            coordinator=project_coordinator,
            synthesis_proposals=synthesis_proposal_store,
            retrieval=project_retrieval_service,
        )
    )
    application.include_router(
        create_project_retrieval_router(project_retrieval_service)
    )
    return application


app = create_app()
