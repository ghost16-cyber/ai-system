from __future__ import annotations

from backend.app.assignments.code_blueprints import generate_code_blueprints
from backend.app.assignments.dashboard_spec import generate_dashboard_spec
from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.marking_checker import check_marking_readiness
from backend.app.assignments.report_generator import generate_report_draft
from backend.app.assignments.runbook import generate_assignment_runbook
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentCodeBlueprintSet,
    AssignmentEvidenceChecklist,
    AssignmentMarkingReadiness,
    AssignmentReportDraft,
    AssignmentRunbook,
    DashboardSpec,
    FinalReadinessReport,
)
from backend.app.datasets.schemas import DatasetProfile
from backend.app.workspace.schemas import WorkspaceInspection


def build_final_readiness_report(
    brief: AssignmentBrief,
    *,
    assignment_number: int,
    dataset_profile: DatasetProfile | None = None,
    workspace_inspection: WorkspaceInspection | None = None,
    code_blueprints: AssignmentCodeBlueprintSet | None = None,
    evidence: AssignmentEvidenceChecklist | None = None,
    report_draft: AssignmentReportDraft | None = None,
    marking_readiness: list[AssignmentMarkingReadiness] | None = None,
    dashboard_spec: DashboardSpec | None = None,
    runbook: AssignmentRunbook | None = None,
) -> FinalReadinessReport:
    evidence = evidence or build_evidence_checklist(brief)
    code_blueprints = code_blueprints or generate_code_blueprints(assignment_number, dataset_profile=dataset_profile)
    report_draft = report_draft or generate_report_draft(brief, evidence=evidence)
    marking_readiness = marking_readiness or check_marking_readiness(brief, evidence)
    dashboard_spec = dashboard_spec or generate_dashboard_spec(assignment_number, dataset_profile=dataset_profile)
    runbook = runbook or generate_assignment_runbook(assignment_number)

    missing_screenshots = [
        item.title
        for item in evidence.items
        if item.required and item.priority == "blocker" and item.evidence_type in {"screenshot", "dashboard", "terminal_output", "validation_query"} and item.status not in {"provided", "verified"}
    ]
    missing_report = [
        item.title
        for item in evidence.items
        if item.required and item.priority == "blocker" and item.evidence_type == "report_answer" and item.status not in {"provided", "verified"}
    ]
    missing_code = _missing_code_files(code_blueprints, workspace_inspection)
    dataset_risks = _dataset_risks(assignment_number, dataset_profile)
    missing_blockers = []
    missing_blockers.extend(missing_screenshots)
    missing_blockers.extend(missing_code)
    missing_blockers.extend(missing_report)
    missing_blockers.extend(dataset_risks)
    missing_blockers.extend(item for readiness in marking_readiness for item in readiness.missing_critical_items)
    readiness_level = _readiness_level(evidence, missing_blockers, dataset_risks)
    return FinalReadinessReport(
        readiness_level=readiness_level,
        missing_blockers=_limited_blockers(_unique(missing_blockers)),
        missing_screenshots=_unique(missing_screenshots),
        missing_code_files=_unique(missing_code),
        missing_report_sections=_unique(missing_report),
        dataset_risks=dataset_risks,
        command_runbook_summary=[f"{step.step_id}: {step.title}" for step in runbook.steps],
        next_best_action=_next_action(missing_screenshots, missing_code, missing_report, dataset_risks),
        final_submission_checklist=[
            "Confirm dataset suitability and document any limitations.",
            "Run commands manually from the runbook and capture evidence.",
            "Attach every required screenshot with clear filenames.",
            "Complete analysis answers using actual observed outputs.",
            "Review marking readiness before submission.",
            f"Confirm dashboard screenshot requirements: {', '.join(dashboard_spec.screenshot_requirements)}",
            "Ready for review is not a guarantee of full marks.",
        ],
    )


def _missing_code_files(blueprints: AssignmentCodeBlueprintSet, inspection: WorkspaceInspection | None) -> list[str]:
    if inspection is None:
        return [item.file_path for item in blueprints.blueprints if item.file_path.endswith(".py") or item.file_path.endswith(".yml")]
    existing = set(inspection.detected_files)
    return [item.file_path for item in blueprints.blueprints if item.file_path not in existing]


def _dataset_risks(assignment_number: int, profile: DatasetProfile | None) -> list[str]:
    if profile is None:
        return ["Dataset has not been profiled."]
    key = f"assignment_{assignment_number}_suitable"
    if getattr(profile.suitability, key) is True:
        return []
    return [f"Dataset suitability check failed for Assignment {assignment_number}.", *profile.suitability.reasons]


def _readiness_level(evidence: AssignmentEvidenceChecklist, blockers: list[str], dataset_risks: list[str]) -> str:
    if dataset_risks:
        return "in_progress"
    if blockers:
        provided = sum(1 for item in evidence.items if item.status in {"provided", "verified"})
        return "in_progress" if provided else "not_started"
    return "ready_for_review"


def _next_action(screenshots: list[str], code: list[str], report: list[str], dataset: list[str]) -> str:
    if dataset:
        return "Profile or choose a dataset that fits the selected assignment before implementation."
    if code:
        return f"Create or verify the missing code file: {code[0]}."
    if screenshots:
        return f"Capture and mark evidence for: {screenshots[0]}."
    if report:
        return f"Complete the report section: {report[0]}."
    return "Review the final package and ask for feedback before submission."


def _unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _limited_blockers(blockers: list[str], *, limit: int = 10) -> list[str]:
    if len(blockers) <= limit:
        return blockers
    remaining = len(blockers) - limit
    return [*blockers[:limit], f"{remaining} more missing evidence items"]
