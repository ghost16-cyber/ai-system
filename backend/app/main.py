from __future__ import annotations

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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
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
from backend.app.benchmark.trace_compactor import compact_orchestrator_trace
from backend.app.commands import (
    CommandExecutionError,
    analyze_command,
    approve_assignment_command,
    execute_assignment_command,
    get_assignment_command,
    get_assignment_execution_summary,
    plan_assignment_command,
    suggest_assignment_actions,
    suggest_command,
)
from backend.app.core.path_utils import resolve_user_path
from backend.app.chat_workflow import run_chat_workflow
from backend.app.database.repository import AnalysisRepository
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


class AssignmentCommandExecuteRequest(BaseModel):
    assignment_id: str = Field(..., min_length=1, max_length=128)
    workspace_path: str = Field(..., min_length=1)
    approval_token: str = Field(..., min_length=1)


class DebugAnalyzeErrorRequest(BaseModel):
    output: str = ""
    project_path: str | None = None


def create_app(
    database_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
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

    repository = AnalysisRepository(configured_path)
    job_queue = JobQueue(configured_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        job_queue.initialize()
        yield

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
    application.state.job_queue = job_queue
    application.state.workspace_root = configured_workspace_root
    application.include_router(specialists_router)

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

    @application.get("/assignments/status")
    def assignment_status() -> dict:
        return {
            "status": "ready",
            "supported_extensions": [".txt", ".md", ".docx"],
            "supported_dataset_extensions": [".csv", ".txt", ".tsv"],
            "features": ["parse", "extract", "plan", "template_plan", "template_write"],
            "advisory_only": True,
            "tools_executed": False,
            "patches_applied": False,
            "runtime_authorized": False,
        }

    @application.post("/assignments/parse")
    def assignment_parse(request: AssignmentParseRequest) -> dict:
        try:
            parsed = parse_assignment_document(_resolve_assignment_path(request.path))
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
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
            workspace_path = _resolve_workspace_path(request.workspace_path) if request.workspace_path else None
            document_path = _resolve_assignment_path(request.path) if request.path else None
            dataset_profile = (
                profile_csv_dataset(_resolve_dataset_path(request.dataset_path))
                if request.dataset_path
                else None
            )
            result = run_assignment_copilot(
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
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.model_dump(mode="json")

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
            root = _resolve_workspace_write_path(request.workspace_path)
            copilot_mode = request.copilot_result.get("generation_mode")
            if (
                copilot_mode is not None
                and copilot_mode != request.generation_mode
            ):
                raise ValueError(
                    "Requested generation_mode does not match the Copilot result."
                )
            raw_blueprints = request.copilot_result.get(
                "grounded_file_blueprints",
                [],
            )
            if not isinstance(raw_blueprints, list):
                raise ValueError(
                    "copilot_result grounded_file_blueprints must be a list."
                )
            blueprints = [
                GroundedFileBlueprint.model_validate(item)
                for item in raw_blueprints
                if isinstance(item, dict)
                and int(item.get("assignment_number", 0))
                == request.assignment_number
            ]
            if not blueprints:
                raise ValueError(
                    "No grounded file blueprints were provided for the selected assignment."
                )
            raw_summaries = request.copilot_result.get(
                "corpus_grounding_summary",
                [],
            )
            raw_plans = request.copilot_result.get(
                "workspace_generation_plan",
                [],
            )
            summary_index = next(
                (
                    index
                    for index, item in enumerate(raw_plans)
                    if isinstance(item, dict)
                    and int(item.get("assignment_number", 0))
                    == request.assignment_number
                ),
                None,
            ) if isinstance(raw_plans, list) else None
            summary_payload = (
                raw_summaries[summary_index]
                if isinstance(raw_summaries, list)
                and summary_index is not None
                and len(raw_summaries) > summary_index
                else {}
            )
            summary = CorpusGroundingSummary.model_validate(
                summary_payload
            )
            result = write_grounded_workspace(
                root,
                blueprints,
                grounding_summary=summary,
                overwrite=request.overwrite,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            **result.model_dump(mode="json"),
            "generation_mode": request.generation_mode,
        }

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
            supported_extensions={".txt", ".md", ".docx"},
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
        return {"plan": plan, "approval_token": approval_token}

    @application.post("/assignments/commands/{plan_id}/execute")
    def assignment_command_execute(
        plan_id: str,
        request: AssignmentCommandExecuteRequest,
    ) -> dict:
        try:
            workspace = _resolve_workspace_path(request.workspace_path)
            return execute_assignment_command(
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

    @application.post("/chat/run", response_model=ChatRunResponse)
    def chat_run(request: ChatRunRequest) -> ChatRunResponse:
        previous_turns = (
            repository.list_chat_runs_for_conversation(request.conversation_id)
            if request.conversation_id
            else []
        )
        run = run_chat_workflow(
            request,
            workspace_root=configured_workspace_root,
            previous_turns=previous_turns,
        )
        repository.store_chat_run(run)
        _log_training_example(run)
        return run

    @application.post("/chat/stream")
    def chat_stream(request: ChatRunRequest) -> StreamingResponse:
        previous_turns = (
            repository.list_chat_runs_for_conversation(request.conversation_id)
            if request.conversation_id
            else []
        )
        events: queue.Queue[dict | object] = queue.Queue()
        done = object()

        def worker() -> None:
            try:
                run = run_chat_workflow(
                    request,
                    workspace_root=configured_workspace_root,
                    previous_turns=previous_turns,
                    event_sink=events.put,
                )
                repository.store_chat_run(run)
                _log_training_example(run)
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
                events.put(
                    {
                        "event": "run_failed",
                        "data": {"error": str(error)},
                    }
                )
            finally:
                events.put(done)

        def event_stream():
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

    @application.get(
        "/chat/conversations/{conversation_id}",
        response_model=ChatConversationDetail,
    )
    def chat_conversation(conversation_id: str) -> ChatConversationDetail:
        try:
            return repository.get_chat_conversation(conversation_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

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

    return application


app = create_app()
