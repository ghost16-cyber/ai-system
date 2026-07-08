from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class FixValidationResponse(BaseModel):
    status: Literal["not_available", "passed", "failed"] = "not_available"
    checks: list[str] = Field(default_factory=list)
    message: str = "No automatic fix is available for this finding."


class IssueResponse(BaseModel):
    finding_id: str | None = None
    rule_id: str
    source: str = "static_rule"
    category: str
    severity: str
    message: str
    suggestion: str
    line: int | None = None
    column: int | None = None
    suggested_code: str | None = None
    validation: FixValidationResponse = Field(default_factory=FixValidationResponse)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    phase: str
    database: str
    timestamp: datetime


class AnalyzeRequest(BaseModel):
    code: str = Field(..., min_length=1)
    language: str = "python"
    filename: str | None = None


class AnalyzeFileRequest(BaseModel):
    path: str = Field(..., min_length=1)


class AnalyzeProjectRequest(BaseModel):
    path: str = Field(default=".", min_length=1)


class OrchestrateRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    path: str = Field(default=".", min_length=1)
    allow_edits: bool = False
    allow_tests: bool = True
    approval_mode: Literal["auto", "review", "never"] = "auto"
    allow_dirty_worktree: bool = False
    rollback_on_test_failure: bool = True
    max_patch_changed_lines: int = Field(default=20, ge=1, le=50)
    allowed_patch_files: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=12, ge=1, le=50)
    proposer: Literal["scripted", "slm"] = "scripted"
    advisor_runtime_mode: Literal["off", "shadow", "ranking_boost", "guarded_action"] = "off"
    slm_model: str = "qwen2.5-coder:1.5b"
    slm_base_url: str = "http://localhost:11434"


class RuntimePlanValidationRequest(BaseModel):
    task: str = Field(..., min_length=1)
    requested_plan: dict[str, Any] = Field(default_factory=dict)


class ExecutionProfileRequest(BaseModel):
    task: str = Field(..., min_length=1)
    requested_plan: dict[str, Any] = Field(default_factory=dict)


class ChatRunRequest(BaseModel):
    message: str = Field(..., min_length=1)
    use_rag: bool = True
    safety_mode: str = "read_only"
    conversation_id: str | None = None


class ChatRunResponse(BaseModel):
    run_id: str
    conversation_id: str
    user_message: str
    assistant_response: str
    selected_specialist: str
    intent: str
    confidence: float
    rag_used: bool
    rag_skip_reason: str | None = None
    rag_context_count: int
    runtime_decision: str
    safety_decision: str
    used_real_slm: bool = False
    slm_provider: str = "fallback"
    slm_model: str | None = None
    slm_fallback_reason: str | None = None
    slm_latency_ms: int | None = None
    memory_used: bool = False
    memory_summary: str | None = None
    created_at: datetime
    trace_summary: list[dict[str, Any]] = Field(default_factory=list)



class ChatRunsResponse(BaseModel):
    items: list[ChatRunResponse]


class ChatConversationSummary(BaseModel):
    conversation_id: str
    title: str
    first_user_message: str
    turn_count: int
    latest_timestamp: datetime
    latest_specialist: str
    latest_rag_used: bool
    latest_rag_skip_reason: str | None = None
    latest_safety_decision: str
    latest_runtime_decision: str
    memory_summary: str | None = None


class ChatConversationsResponse(BaseModel):
    items: list[ChatConversationSummary]


class ChatConversationDetail(BaseModel):
    conversation_id: str
    title: str
    memory_summary: str | None = None
    turns: list[ChatRunResponse]


class ChatConversationDeleteResponse(BaseModel):
    conversation_id: str
    deleted: bool
    deleted_turns: int


PatchProposalStatus = Literal["proposed", "applied", "conflict"]


class PatchProposalResponse(BaseModel):
    proposal_id: str
    analysis_id: str
    finding_id: str
    path: str
    original_file_sha256: str
    start_line: int
    end_line: int
    replacement: str
    validation_status: Literal["passed"]
    status: PatchProposalStatus = "proposed"


class PatchPreviewRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1)


class PatchPreviewResponse(BaseModel):
    proposal_id: str
    path: str
    status: PatchProposalStatus
    unified_diff: str | None = None
    original_file_sha256: str
    current_file_sha256: str
    preview_available: bool


class PatchApplyRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1)
    run_pytest: bool = False


PatchVerificationStatus = Literal["not_requested", "passed", "failed", "error"]


class PatchVerificationResponse(BaseModel):
    requested: bool = False
    tool: Literal["pytest"] | None = None
    status: PatchVerificationStatus = "not_requested"
    exit_code: int | None = None
    output: str | None = None
    checked_at: datetime | None = None


class PatchApplyResponse(BaseModel):
    proposal_id: str
    analysis_id: str
    finding_id: str
    path: str
    status: Literal["applied"]
    applied: bool
    original_file_sha256: str
    updated_file_sha256: str
    message: str
    verification: PatchVerificationResponse = Field(
        default_factory=PatchVerificationResponse
    )


class AnalyzeResponse(BaseModel):
    analysis_id: str
    success: bool
    language: str
    filename: str | None
    issues: list[IssueResponse]
    suggestions: list[str]
    patch_proposals: list[PatchProposalResponse] = Field(default_factory=list)
    metadata: dict[str, Any]
    created_at: datetime


class RuleMetadataResponse(BaseModel):
    rule_id: str
    category: str
    severity: str
    description: str
    suggestion: str
    source: str = "static_rule"
    fix_available: bool = False


class RulesResponse(BaseModel):
    items: list[RuleMetadataResponse]


class ToolMetadataResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    execution: Literal["synchronous", "job_backed"]


class ToolsResponse(BaseModel):
    items: list[ToolMetadataResponse]


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobResponse(BaseModel):
    job_id: str
    job_type: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None


class JobsResponse(BaseModel):
    items: list[JobResponse]


class JobAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    status_url: str


class AnalysisHistoryItem(BaseModel):
    analysis_id: str
    created_at: datetime
    code_sha256: str
    language: str
    filename: str | None
    code_length: int
    line_count: int
    issue_count: int
    phase: str


class HistoryResponse(BaseModel):
    items: list[AnalysisHistoryItem]


class FeedbackRequest(BaseModel):
    analysis_id: str = Field(..., min_length=1)
    finding_id: str = Field(..., min_length=1)
    helpful: bool
    suggestion_accepted: bool | None = None


class FeedbackResponse(BaseModel):
    feedback_id: str
    analysis_id: str
    finding_id: str
    helpful: bool
    suggestion_accepted: bool | None
    created_at: datetime


class MetricsResponse(BaseModel):
    phase: str
    total_analyses: int
    analyses_with_findings: int
    clean_analyses: int
    total_findings: int
    average_findings_per_analysis: float
    parse_failures: int
    validated_fixes: int
    fixable_findings: int
    validated_fix_rate: float
    findings_without_fix: int
    fixes_by_rule: dict[str, int]
    findings_by_rule: dict[str, int]
    findings_by_severity: dict[str, int]
    validation_statuses: dict[str, int]
    total_feedback: int
    helpful_feedback: int
    unhelpful_feedback: int
    accepted_suggestions: int
    rejected_suggestions: int
    suggestion_acceptance_rate: float | None
