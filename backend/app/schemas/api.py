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


class PatchApplyRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1)


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
