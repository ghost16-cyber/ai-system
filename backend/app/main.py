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
from backend.app.benchmark.trace_compactor import compact_orchestrator_trace
from backend.app.chat_workflow import run_chat_workflow
from backend.app.database.repository import AnalysisRepository
from backend.app.hardware_ai_optimizer import (
    HardwareOptimizerResponse,
    probe_hardware,
    recommend_training_settings,
)
from backend.app.jobs import JobQueue
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
from backend.app.rag.corpus_index_preview import build_corpus_index_preview
from backend.app.rag.corpus_text_extractor import extract_indexable_corpus
from backend.app.rag.corpus_chunker import build_corpus_chunk_preview
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


class SLMChatWithContextRequest(BaseModel):
    message: str = ""
    limit: int = Field(default=4, ge=0, le=10)
    source_filter: str | None = None


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

    @application.get("/rag/status")
    def local_rag_status() -> dict:
        return rag_status(configured_workspace_root)

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
