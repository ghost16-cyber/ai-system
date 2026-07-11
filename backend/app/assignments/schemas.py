from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.rag.corpus_retrieval import CorpusSourceMetadata


class ParsedAssignmentDocument(BaseModel):
    document_id: str
    title: str
    source_path: str
    extracted_text: str
    created_at: datetime
    warnings: list[str] = Field(default_factory=list)


class AssignmentTask(BaseModel):
    task_id: str
    title: str
    description: str
    technologies: list[str] = Field(default_factory=list)
    marks: float | None = None
    required_output: str | None = None
    optional: bool = False


class ScreenshotRequirement(BaseModel):
    requirement_id: str
    description: str
    assignment_name: str | None = None
    task_name: str | None = None


class MarkingCriterion(BaseModel):
    criterion_id: str
    description: str
    marks: float | None = None
    assignment_name: str | None = None


class AnalysisQuestion(BaseModel):
    question_id: str
    question: str
    assignment_name: str | None = None


class AssignmentSection(BaseModel):
    section_id: str
    title: str
    technologies: list[str] = Field(default_factory=list)
    tasks: list[AssignmentTask] = Field(default_factory=list)
    screenshot_requirements: list[ScreenshotRequirement] = Field(default_factory=list)
    marking_criteria: list[MarkingCriterion] = Field(default_factory=list)
    analysis_questions: list[AnalysisQuestion] = Field(default_factory=list)
    bonus_requirements: list[AssignmentTask] = Field(default_factory=list)
    dataset_requirements: list[str] = Field(default_factory=list)
    report_requirements: list[str] = Field(default_factory=list)
    global_instructions: list[str] = Field(default_factory=list)
    report_guidance: list[str] = Field(default_factory=list)


class AssignmentBrief(BaseModel):
    title: str
    technologies: list[str] = Field(default_factory=list)
    sections: list[AssignmentSection] = Field(default_factory=list)
    screenshot_requirements: list[ScreenshotRequirement] = Field(default_factory=list)
    marking_criteria: list[MarkingCriterion] = Field(default_factory=list)
    analysis_questions: list[AnalysisQuestion] = Field(default_factory=list)
    bonus_requirements: list[AssignmentTask] = Field(default_factory=list)
    dataset_requirements: list[str] = Field(default_factory=list)
    report_requirements: list[str] = Field(default_factory=list)
    global_instructions: list[str] = Field(default_factory=list)
    report_guidance: list[str] = Field(default_factory=list)


ChecklistStatus = Literal["todo", "doing", "done", "blocked"]


class AssignmentChecklistItem(BaseModel):
    task_id: str
    title: str
    assignment_name: str
    technology_area: str
    required_output: str
    evidence_needed: list[str] = Field(default_factory=list)
    screenshot_needed: bool = False
    report_section_needed: bool = False
    status: ChecklistStatus = "todo"
    optional: bool = False
    group: str = "Pipeline/code implementation"


class AssignmentPlan(BaseModel):
    title: str
    checklist: list[AssignmentChecklistItem]
    groups: dict[str, list[AssignmentChecklistItem]]
    summary_groups: list[str]


class AssignmentTemplateFile(BaseModel):
    file_path: str
    purpose: str
    content_preview: str
    technology_area: str
    assignment_number: int
    safe_to_create: bool = True


class AssignmentTemplatePlan(BaseModel):
    assignment_number: int
    assignment_name: str
    files: list[AssignmentTemplateFile]
    warnings: list[str] = Field(default_factory=list)


class AssignmentTemplateWriteResult(BaseModel):
    workspace_root: str
    created_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    refused_files: list[str] = Field(default_factory=list)
    overwrite: bool = False


EvidenceType = Literal[
    "screenshot",
    "terminal_output",
    "code_file",
    "dashboard",
    "report_answer",
    "appendix",
    "validation_query",
]
EvidenceStatus = Literal["missing", "provided", "verified", "rejected"]
EvidencePriority = Literal["blocker", "normal", "optional"]


class AssignmentEvidenceItem(BaseModel):
    evidence_id: str
    assignment_name: str
    task_name: str
    evidence_type: EvidenceType
    title: str
    description: str
    required: bool = True
    source_requirement: str
    status: EvidenceStatus = "missing"
    priority: EvidencePriority = "blocker"
    marks: float | None = None
    rubric_reference: str | None = None
    suggested_filename: str
    notes: str = ""


class AssignmentEvidenceSummary(BaseModel):
    total_required: int
    total_optional: int = 0
    missing_count: int
    required_missing_count: int = 0
    optional_missing_count: int = 0
    provided_count: int
    verified_count: int
    by_assignment: dict[str, dict[str, int]]
    by_evidence_type: dict[str, int]


class AssignmentEvidenceChecklist(BaseModel):
    title: str
    items: list[AssignmentEvidenceItem]
    required_items: list[AssignmentEvidenceItem] = Field(default_factory=list)
    optional_items: list[AssignmentEvidenceItem] = Field(default_factory=list)
    summary: AssignmentEvidenceSummary


class ReportSectionDraft(BaseModel):
    section_id: str
    title: str
    content: str
    needs_user_evidence: bool = False


class AssignmentReportDraft(BaseModel):
    title: str
    sections: list[ReportSectionDraft]
    markdown: str
    warnings: list[str] = Field(default_factory=list)


DifficultyLevel = Literal["easy", "medium", "hard"]


class AssignmentTaskBreakdownItem(BaseModel):
    order: int
    assignment_name: str
    task_id: str
    title: str
    explanation: str
    expected_output: str
    related_evidence: list[str] = Field(default_factory=list)
    difficulty: DifficultyLevel = "medium"
    optional: bool = False


class AssignmentTaskBreakdown(BaseModel):
    title: str
    tasks: list[AssignmentTaskBreakdownItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


CriterionStatus = Literal["missing", "partial", "ready", "optional"]


class MarkingCriterionResult(BaseModel):
    criterion_id: str
    title: str
    marks: float
    status: CriterionStatus
    evidence_required: bool
    report_required: bool
    reason: str
    next_action: str


class AssignmentMarkingReadiness(BaseModel):
    assignment_name: str
    total_marks_available: float
    estimated_ready_marks: float
    missing_critical_items: list[str]
    criterion_results: list[MarkingCriterionResult]
    advisory_note: str = "Estimated readiness only; this is not a guaranteed grade."


class AssignmentCopilotResult(BaseModel):
    parsed_document_summary: dict[str, Any]
    extracted_assignment_sections: list[AssignmentSection]
    action_plan: AssignmentPlan
    recommended_starter_files: list[AssignmentTemplatePlan]
    evidence_checklist: AssignmentEvidenceChecklist
    safe_next_commands: list[dict[str, Any]]
    report_draft: AssignmentReportDraft
    report_skeleton: AssignmentReportDraft | None = None
    task_breakdown: AssignmentTaskBreakdown | None = None
    marking_readiness: list[AssignmentMarkingReadiness]
    next_recommended_step: str
    workspace_inspection: dict[str, Any] | None = None
    dataset_profile: dict[str, Any] | None = None
    workspace_build_plans: list[dict[str, Any]] = Field(default_factory=list)
    runbooks: list[dict[str, Any]] = Field(default_factory=list)
    code_blueprints: list[dict[str, Any]] = Field(default_factory=list)
    analysis_plans: list[dict[str, Any]] = Field(default_factory=list)
    dashboard_specs: list[dict[str, Any]] = Field(default_factory=list)
    final_readiness: dict[str, Any] | None = None
    corpus_retrieval_used: bool = False
    corpus_retrieval_skip_reason: str | None = None
    corpus_context_count: int = 0
    corpus_sources: list[CorpusSourceMetadata] = Field(default_factory=list)
    workspace_generation_plan: list[GroundedWorkspaceGenerationPlan] = Field(default_factory=list)
    grounded_file_blueprints: list[GroundedFileBlueprint] = Field(default_factory=list)
    corpus_grounding_summary: list[CorpusGroundingSummary] = Field(default_factory=list)
    unsupported_components: list[str] = Field(default_factory=list)
    generation_warnings: list[str] = Field(default_factory=list)
    generation_ready: bool = False
    generation_mode: GenerationMode = "mixed"
    tools_executed: bool = False
    files_written: bool = False
    training_performed: bool = False


class AssignmentWorkspaceBuildPlan(BaseModel):
    assignment_number: int
    assignment_name: str
    workspace_root: str
    folders_to_create: list[str] = Field(default_factory=list)
    files_to_create: list[AssignmentTemplateFile] = Field(default_factory=list)
    files_to_skip: list[str] = Field(default_factory=list)
    dataset_copy_or_reference_plan: str
    config_files_needed: list[str] = Field(default_factory=list)
    commands_to_run_manually: list[dict[str, Any]] = Field(default_factory=list)
    screenshots_to_capture: list[str] = Field(default_factory=list)
    report_sections_to_complete: list[str] = Field(default_factory=list)
    risks_warnings: list[str] = Field(default_factory=list)
    write_result: AssignmentTemplateWriteResult | None = None
    files_written: bool = False


class AssignmentRunbookStep(BaseModel):
    step_id: str
    title: str
    explanation: str
    command_suggestion: dict[str, Any] | None = None
    expected_result: str
    screenshot_to_take: str | None = None
    troubleshooting_hint: str


class AssignmentRunbook(BaseModel):
    assignment_number: int
    title: str
    steps: list[AssignmentRunbookStep]
    commands_executed: bool = False


class AssignmentReportExportResult(BaseModel):
    output_directory: str
    created_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    refused_files: list[str] = Field(default_factory=list)
    overwrite: bool = False
    warnings: list[str] = Field(default_factory=list)


class AssignmentCodeBlueprint(BaseModel):
    file_path: str
    purpose: str
    assignment_number: int
    technology_area: str
    required_inputs: list[str] = Field(default_factory=list)
    generated_content: str
    placeholders: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    expected_screenshot_links: list[str] = Field(default_factory=list)


class AssignmentCodeBlueprintSet(BaseModel):
    assignment_number: int
    blueprints: list[AssignmentCodeBlueprint]
    warnings: list[str] = Field(default_factory=list)


class AssignmentCodeWriteResult(BaseModel):
    workspace_path: str
    created_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    refused_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_manual_steps: list[str] = Field(default_factory=list)
    overwrite: bool = False
    commands_executed: bool = False
    credentials_written: bool = False


GenerationMode = Literal["template_only", "corpus_grounded", "mixed"]


class GroundingProvenance(BaseModel):
    source_path: str
    chunk_id: str
    chunk_index: int
    score: float
    start_line: int | None = None
    end_line: int | None = None
    influence: str


class GroundedFileBlueprint(BaseModel):
    file_path: str
    purpose: str
    assignment_number: int
    technology_area: str
    generation_mode: Literal["template_only", "corpus_grounded"]
    source_ids: list[str] = Field(default_factory=list)
    grounding: list[GroundingProvenance] = Field(default_factory=list)
    generated_content: str
    warnings: list[str] = Field(default_factory=list)


class GroundedWorkspaceFilePlan(BaseModel):
    path: str
    purpose: str
    generation_mode: Literal["template_only", "corpus_grounded"]
    source_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GroundedWorkspaceGenerationPlan(BaseModel):
    assignment_number: int
    assignment_title: str
    workspace_path: str
    generation_mode: GenerationMode
    technologies: list[str] = Field(default_factory=list)
    directories: list[str] = Field(default_factory=list)
    files: list[GroundedWorkspaceFilePlan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_requirements: list[str] = Field(default_factory=list)
    evidence_placeholders: list[str] = Field(default_factory=list)
    report_placeholders: list[str] = Field(default_factory=list)
    recommended_manual_configuration_steps: list[str] = Field(default_factory=list)
    commands_executed: bool = False


class CorpusGroundingSummary(BaseModel):
    candidate_source_count: int = 0
    usable_source_count: int = 0
    grounded_file_count: int = 0
    template_file_count: int = 0
    excluded_source_count: int = 0
    source_paths: list[str] = Field(default_factory=list)


class GroundedGenerationResult(BaseModel):
    workspace_generation_plan: GroundedWorkspaceGenerationPlan
    grounded_file_blueprints: list[GroundedFileBlueprint] = Field(default_factory=list)
    corpus_grounding_summary: CorpusGroundingSummary
    unsupported_components: list[str] = Field(default_factory=list)
    generation_warnings: list[str] = Field(default_factory=list)
    generation_ready: bool = False
    generation_mode: GenerationMode = "mixed"


class GroundedWorkspaceWriteResult(BaseModel):
    workspace_path: str
    created_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    refused_files: list[str] = Field(default_factory=list)
    grounding_summary: CorpusGroundingSummary
    warnings: list[str] = Field(default_factory=list)
    overwrite: bool = False
    commands_executed: bool = False
    generated_code_executed: bool = False


class DatasetMappingSuggestion(BaseModel):
    column: str
    reason: str
    placeholder: bool = False


class AssignmentDatasetMapping(BaseModel):
    dataset_path: str | None = None
    timestamp_column: DatasetMappingSuggestion
    primary_numeric_indicator: DatasetMappingSuggestion
    secondary_numeric_fields: list[DatasetMappingSuggestion] = Field(default_factory=list)
    category_grouping_column: DatasetMappingSuggestion
    classification_threshold_idea: str
    dashboard_filter_column: DatasetMappingSuggestion
    spark_aggregation_columns: list[str] = Field(default_factory=list)
    snowflake_table_names: list[str] = Field(default_factory=list)
    redis_key_patterns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    placeholders_used: bool = False


class AssignmentProjectManifest(BaseModel):
    assignment_number: int
    dataset_path: str | None = None
    document_path: str | None = None
    generated_files: list[str] = Field(default_factory=list)
    report_files: list[str] = Field(default_factory=list)
    evidence_checklist: dict[str, Any] = Field(default_factory=dict)
    task_breakdown: dict[str, Any] = Field(default_factory=dict)
    report_skeleton: dict[str, Any] = Field(default_factory=dict)
    runbook_steps: list[dict[str, Any]] = Field(default_factory=list)
    safe_commands: list[dict[str, Any]] = Field(default_factory=list)
    missing_screenshots: list[str] = Field(default_factory=list)
    missing_report_sections: list[str] = Field(default_factory=list)
    readiness_level: str = "not_started"
    last_updated: datetime
    tools_executed: bool = False
    files_written: bool = False
    credentials_included: bool = False


class AssignmentManifestWriteResult(BaseModel):
    workspace_path: str
    manifest_path: str
    written: bool
    skipped: bool = False
    refused: bool = False
    warnings: list[str] = Field(default_factory=list)
    overwrite: bool = False


class AssignmentAnalysisQuestion(BaseModel):
    question_id: str
    assignment_number: int
    question: str
    method: str
    suggested_logic: str
    expected_output_columns: list[str] = Field(default_factory=list)
    report_prompt: str


class AssignmentAnalysisPlan(BaseModel):
    assignment_number: int
    questions: list[AssignmentAnalysisQuestion]
    warnings: list[str] = Field(default_factory=list)


class DashboardChartSpec(BaseModel):
    chart_id: str
    title: str
    chart_type: str
    data_fields: list[str] = Field(default_factory=list)
    purpose: str


class DashboardSpec(BaseModel):
    assignment_number: int
    dashboard_title: str
    dashboard_type: str
    data_source: str
    required_filters: list[str] = Field(default_factory=list)
    kpi_cards: list[str] = Field(default_factory=list)
    charts: list[DashboardChartSpec] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    refresh_behavior: str
    screenshot_requirements: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)


ReadinessLevel = Literal["not_started", "in_progress", "almost_ready", "ready_for_review"]


class FinalReadinessReport(BaseModel):
    readiness_level: ReadinessLevel
    missing_blockers: list[str] = Field(default_factory=list)
    missing_screenshots: list[str] = Field(default_factory=list)
    missing_code_files: list[str] = Field(default_factory=list)
    missing_report_sections: list[str] = Field(default_factory=list)
    dataset_risks: list[str] = Field(default_factory=list)
    command_runbook_summary: list[str] = Field(default_factory=list)
    next_best_action: str
    final_submission_checklist: list[str] = Field(default_factory=list)
    advisory_note: str = "Ready for review is not a guaranteed grade or full marks."
