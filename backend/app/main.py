from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query

from backend.app.analyzer import add_validated_fixes, analyze_python_code
from backend.app.analyzer.rules.metadata import get_rule_metadata
from backend.app.database.repository import AnalysisRepository
from backend.app.schemas.api import (
    AnalyzeFileRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    HistoryResponse,
    MetricsResponse,
    RulesResponse,
    ToolsResponse,
)
from backend.app.tools import get_tool_metadata


APP_VERSION = "0.5.0"
APP_PHASE = "release-4-feedback"
DEFAULT_DATABASE_PATH = Path("data/app/ai_system.db")
DEFAULT_WORKSPACE_ROOT = Path.cwd()


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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.initialize()
        yield

    application = FastAPI(
        title="AI Coding Assistant",
        description="Local-first Python code analysis service.",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.analysis_repository = repository
    application.state.workspace_root = configured_workspace_root

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

    def analyze_source(code: str, filename: str | None) -> AnalyzeResponse:
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

        return AnalyzeResponse(
            analysis_id=analysis_id,
            success=True,
            language="python",
            filename=filename,
            issues=findings,
            suggestions=suggestions,
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

        return analyze_source(code, relative_path.as_posix())

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

    return application


app = create_app()
