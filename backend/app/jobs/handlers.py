from __future__ import annotations

from collections import Counter
from pathlib import Path

from backend.app.local_ai.config import load_local_ai_configuration

from backend.app.analyzer import analyze_python_code
from backend.app.orchestrator import JsonlTraceStore, Orchestrator, OrchestratorConfig
from backend.app.repo_scanner import scan_repository

from .worker import JobHandler


def build_job_handlers(workspace_root: str | Path) -> dict[str, JobHandler]:
    root = Path(workspace_root).expanduser().resolve()
    return {
        "analyze_project": lambda payload: analyze_project_job(payload, root),
        "orchestrate_task": lambda payload: orchestrate_task_job(payload, root),
    }


def analyze_project_job(
    payload: dict[str, object],
    workspace_root: Path,
) -> dict[str, object]:
    requested_path = Path(str(payload.get("path", ".")))
    if requested_path.is_absolute():
        raise ValueError("Project path must be relative to the configured workspace root.")

    project_root = (workspace_root / requested_path).resolve()
    try:
        relative_root = project_root.relative_to(workspace_root)
    except ValueError as error:
        raise ValueError(
            "Project path must stay within the configured workspace root."
        ) from error
    if not project_root.exists() or not project_root.is_dir():
        raise ValueError("Project directory was not found.")

    scan = scan_repository(project_root, include_ast=False)
    findings_by_rule: Counter[str] = Counter()
    findings_by_severity: Counter[str] = Counter()
    file_results: list[dict[str, object]] = []
    read_errors: list[dict[str, str]] = []

    for scanned_file in scan["files"]:
        if scanned_file["language"] != "python":
            continue

        candidate = (project_root / scanned_file["path"]).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            read_errors.append(
                {
                    "path": _workspace_path(relative_root, scanned_file["path"]),
                    "error": "Resolved file path escaped the configured workspace root.",
                }
            )
            continue

        reported_path = _workspace_path(relative_root, scanned_file["path"])
        try:
            code = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            read_errors.append(
                {"path": reported_path, "error": "Python file must be UTF-8 encoded."}
            )
            continue

        analysis = analyze_python_code(code)
        issues = [
            issue.model_dump(exclude={"finding_id", "suggested_code"})
            for issue in analysis.issues
        ]
        for issue in analysis.issues:
            findings_by_rule[issue.rule_id] += 1
            findings_by_severity[issue.severity] += 1
        file_results.append(
            {
                "path": reported_path,
                "parse_success": analysis.parse_success,
                "issues": issues,
            }
        )

    return {
        "path": relative_root.as_posix(),
        "files_discovered": scan["summary"]["total_files"],
        "python_files_analyzed": len(file_results),
        "total_findings": sum(findings_by_rule.values()),
        "findings_by_rule": dict(sorted(findings_by_rule.items())),
        "findings_by_severity": dict(sorted(findings_by_severity.items())),
        "files": file_results,
        "read_errors": read_errors,
        "source_stored": False,
    }


def orchestrate_task_job(
    payload: dict[str, object],
    workspace_root: Path,
) -> dict[str, object]:
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise ValueError("Task goal is required.")

    requested_path = Path(str(payload.get("path", ".")))
    if requested_path.is_absolute():
        raise ValueError("Task path must be relative to the configured workspace root.")

    project_root = (workspace_root / requested_path).resolve()
    try:
        relative_root = project_root.relative_to(workspace_root)
    except ValueError as error:
        raise ValueError("Task path must stay within the configured workspace root.") from error
    if not project_root.exists() or not project_root.is_dir():
        raise ValueError("Task directory was not found.")

    orchestrator = Orchestrator(
        workspace_root=workspace_root,
        trace_store=JsonlTraceStore(Path("data/app/orchestrator_traces.jsonl")),
        config=OrchestratorConfig(
            max_steps=int(payload.get("max_steps", 12)),
            proposer=str(payload.get("proposer", "scripted")),  # type: ignore[arg-type]
            advisor_runtime_mode=str(payload.get("advisor_runtime_mode", "off")),  # type: ignore[arg-type]
            slm_model=str(
                payload.get("slm_model")
                or load_local_ai_configuration().coder_model
            ),
            slm_base_url=str(
                payload.get("slm_base_url")
                or load_local_ai_configuration().endpoint_identity
            ),
            checkpoint_root=str(payload.get("checkpoint_root", "data/app/checkpoints")),
            approval_root=str(payload.get("approval_root", "data/app/pending_approvals")),
        ),
    )
    result = orchestrator.run(
        goal=goal,
        project_path=relative_root.as_posix(),
        allow_edits=bool(payload.get("allow_edits", False)),
        allow_tests=bool(payload.get("allow_tests", True)),
        approval_mode=str(payload.get("approval_mode", "auto")),  # type: ignore[arg-type]
        allow_dirty_worktree=bool(payload.get("allow_dirty_worktree", False)),
        rollback_on_test_failure=bool(payload.get("rollback_on_test_failure", True)),
        max_patch_changed_lines=int(payload.get("max_patch_changed_lines", 20)),
        allowed_patch_files=[
            str(path)
            for path in payload.get("allowed_patch_files", [])
            if isinstance(path, str) and path.strip()
        ],
    )
    return result.model_dump(mode="json")


def _workspace_path(relative_root: Path, scanned_path: str) -> str:
    if relative_root == Path("."):
        return scanned_path
    return (relative_root / scanned_path).as_posix()
