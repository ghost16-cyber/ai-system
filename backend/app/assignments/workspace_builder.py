from __future__ import annotations

from pathlib import Path

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentWorkspaceBuildPlan,
)
from backend.app.assignments.templates import (
    generate_assignment_template_plan,
    write_assignment_template_plan,
)
from backend.app.commands import suggest_command
from backend.app.datasets.schemas import DatasetProfile
from backend.app.workspace import inspect_workspace


def plan_assignment_workspace(
    brief: AssignmentBrief,
    *,
    assignment_number: int,
    workspace_root: str | Path,
    dataset_profile: DatasetProfile | None = None,
    write_files: bool = False,
    overwrite: bool = False,
) -> AssignmentWorkspaceBuildPlan:
    root = Path(workspace_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    template = generate_assignment_template_plan(assignment_number)
    inspection = inspect_workspace(root)
    existing = set(inspection.detected_files)
    files_to_create = [file for file in template.files if file.file_path not in existing]
    files_to_skip = [file.file_path for file in template.files if file.file_path in existing]
    folders = sorted({str(Path(file.file_path).parent).replace("\\", "/") for file in template.files if str(Path(file.file_path).parent) != "."})
    evidence = build_evidence_checklist(brief)
    write_result = None
    if write_files:
        write_result = write_assignment_template_plan(root, template, overwrite=overwrite)
    warnings = _dataset_warnings(assignment_number, dataset_profile)
    warnings.extend("Existing file skipped by default: " + item for item in files_to_skip)
    return AssignmentWorkspaceBuildPlan(
        assignment_number=assignment_number,
        assignment_name=template.assignment_name,
        workspace_root=str(root),
        folders_to_create=folders,
        files_to_create=files_to_create,
        files_to_skip=files_to_skip,
        dataset_copy_or_reference_plan=_dataset_plan(dataset_profile),
        config_files_needed=_config_files(template),
        commands_to_run_manually=[command.model_dump(mode="json") for command in _commands(assignment_number, root)],
        screenshots_to_capture=[item.title for item in evidence.items if item.evidence_type in {"screenshot", "dashboard", "terminal_output", "validation_query"}],
        report_sections_to_complete=["Dataset Description", "Environment Setup", "Implementation Steps", "Screenshots and Evidence", "Analysis Questions", "Marking Checklist"],
        risks_warnings=warnings,
        write_result=write_result,
        files_written=bool(write_result and write_result.created_files),
    )


def _dataset_plan(profile: DatasetProfile | None) -> str:
    if profile is None:
        return "No dataset selected yet. Reference or copy the approved CSV after profiling it."
    return f"Reference `{profile.dataset_path}` from the scripts; avoid copying large data unless submission rules require it."


def _dataset_warnings(assignment_number: int, profile: DatasetProfile | None) -> list[str]:
    if profile is None:
        return ["Dataset has not been profiled yet."]
    key = f"assignment_{assignment_number}_suitable"
    if getattr(profile.suitability, key) is True:
        return [f"Dataset appears suitable for Assignment {assignment_number} based on deterministic checks."]
    return [f"Dataset may not fit Assignment {assignment_number}.", *profile.suitability.reasons]


def _config_files(template) -> list[str]:
    return [
        file.file_path
        for file in template.files
        if "config" in file.file_path.lower() or "env" in file.file_path.lower()
    ]


def _commands(assignment_number: int, root: Path):
    commands = [suggest_command("docker_ps", root)]
    if assignment_number in {1, 3}:
        commands.append(suggest_command("docker_compose_up", root))
        commands.append(suggest_command("python_script", root, target="producer.py" if assignment_number == 1 else "replay_producer.py"))
    if assignment_number == 2:
        commands.append(suggest_command("python_script", root, target="spark_processing.py"))
        commands.append(suggest_command("streamlit", root, target="dashboard/app.py"))
    if assignment_number == 3:
        commands.append(suggest_command("python_script", root, target="structured_streaming_job.py"))
        commands.append(suggest_command("streamlit", root, target="dashboard/app.py"))
    return commands
