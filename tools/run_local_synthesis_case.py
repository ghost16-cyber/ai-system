from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.benchmark.test_output_parser import parse_pytest_output
from backend.app.database.migrations import apply_schema_migrations
from backend.app.project_analysis.model_synthesis import (
    CanonicalProviderProfile,
    CanonicalSynthesisOrchestrator,
    UnavailableSynthesisGateway,
    build_synthesis_gateway_from_environment,
)
from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
)
from backend.app.project_control.contracts import content_hash
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_models import ProjectModelInvocationStore
from backend.app.project_retrieval import ProjectRetrievalService
from backend.app.project_retrieval.bindings import (
    canonical_retrieval_authority_id,
)
from backend.app.project_retrieval.contracts import (
    CorpusIngestionRequest,
    RetrievalRequest,
    normalize_query,
)


CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{2,119}$")
SUITES = {
    "real": ROOT / "benchmarks" / "real_repos",
    "stress": ROOT / "benchmarks" / "real_repo_stress",
}
TEST_TIMEOUT_SECONDS = 30
MAX_REPORT_OUTPUT_TAIL = 1_200


class LocalSynthesisEvaluationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic or {}


@dataclass(frozen=True)
class PreparedEvaluation:
    orchestrator: CanonicalSynthesisOrchestrator
    artifacts: ProjectArtifactStore
    intent: Any
    evidence_artifact: Any
    retrieval_evidence: Any
    allowed_modify_paths: tuple[str, ...]
    file_hashes: dict[str, str]
    retrieval_summary: dict[str, Any]


def _build_disposable_gateway(database_path: Path):
    """Create the gateway only after its disposable canonical schema exists."""
    apply_schema_migrations(database_path)
    return build_synthesis_gateway_from_environment(database_path=database_path)


def evaluate_case(
    case_dir: Path,
    gateway=None,
    *,
    python_executable: str = sys.executable,
    test_timeout_seconds: int = TEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    started = time.monotonic()
    case_dir = case_dir.resolve(strict=True)
    metadata = _load_metadata(case_dir)
    source_snapshot = _directory_hash(case_dir)

    with tempfile.TemporaryDirectory(
        prefix=f"astra-local-synthesis-{metadata['case_id']}-"
    ) as temporary:
        root = Path(temporary)
        if gateway is None:
            gateway = _build_disposable_gateway(root / "astra-evaluation.db")
            if isinstance(gateway, UnavailableSynthesisGateway):
                raise LocalSynthesisEvaluationError(
                    "provider_unavailable",
                    gateway.reason,
                )
            from scripts.astra_phase5b_smoke import (
                _enable_disposable_model_profile,
            )

            _enable_disposable_model_profile(gateway)
        workspace = root / "workspace"
        shutil.copytree(
            case_dir,
            workspace,
            ignore=shutil.ignore_patterns(
                "metadata.json",
                "__pycache__",
                "*.pyc",
                ".pytest_cache",
            ),
        )
        expected_test_file = _safe_relative_path(
            str(metadata["expected_test_file"])
        )
        initial_test = _run_focused_pytest(
            workspace,
            expected_test_file,
            python_executable=python_executable,
            timeout_seconds=test_timeout_seconds,
        )
        if initial_test["status"] != "failed":
            raise LocalSynthesisEvaluationError(
                "invalid_benchmark_case",
                "The selected benchmark case does not fail before synthesis.",
            )

        prepared = _prepare_evaluation(
            root=root,
            workspace=workspace,
            metadata=metadata,
            initial_test=initial_test,
            gateway=gateway,
        )
        profile = CanonicalProviderProfile(
            provider=gateway.provider,
            model_profile=gateway.model,
            endpoint_identity=gateway.endpoint_identity,
        )
        outcome = prepared.orchestrator.prepare_patch(
            prepared.intent,
            prepared.evidence_artifact,
            profile,
            retrieval_evidence=prepared.retrieval_evidence,
        )
        preview = prepared.artifacts.get(str(outcome.artifact_id or ""))
        if (
            preview is None
            or preview.artifact_type != ProjectArtifactType.PATCH_PREVIEW
            or preview.content_hash != outcome.artifact_hash
        ):
            raise LocalSynthesisEvaluationError(
                "missing_preview",
                "Canonical synthesis produced no verifiable preview.",
            )

        application = _apply_disposable_operations(
            workspace,
            tuple(preview.payload.get("operations") or ()),
            allowed_modify_paths=set(prepared.allowed_modify_paths),
        )
        final_test = _run_focused_pytest(
            workspace,
            expected_test_file,
            python_executable=python_executable,
            timeout_seconds=test_timeout_seconds,
        )
        expected_changed_files = {
            _safe_relative_path(str(path))
            for path in metadata.get("expected_changed_files")
            or [metadata.get("expected_source_file")]
            if path
        }
        changed_paths = set(application["changed_paths"])
        verified = (
            final_test["status"] == "passed"
            and bool(changed_paths)
            and not application["unexpected_paths"]
            and application["syntax_valid"]
        )
        generation_diagnostic = _generation_diagnostic(
            prepared.orchestrator,
            outcome,
            gateway,
        )
        result = {
            "schema_version": "astra.local-synthesis.case-evaluation.v1",
            "case_id": metadata["case_id"],
            "suite": "stress" if metadata.get("stress_case") else "real",
            "status": "verified" if verified else "not_verified",
            "verified_repair_success": verified,
            "initial_test": initial_test,
            "final_test": final_test,
            "project_run_id": prepared.intent.project_run_id,
            "coordinator_intent_id": prepared.intent.coordinator_intent_id,
            "provider_identity": gateway.provider,
            "exact_model_tag": gateway.model,
            "expected_response_schema_identity": (
                "astra.project-synthesis.response.v1"
            ),
            "retrieval": prepared.retrieval_summary,
            "application": {
                **application,
                "touched_expected_file": bool(
                    changed_paths & expected_changed_files
                ),
                "touched_unexpected_evaluation_file": bool(
                    changed_paths - expected_changed_files
                ),
            },
            "generation_diagnostic": generation_diagnostic,
            "temporary_preview_artifact_id": preview.artifact_id,
            "temporary_preview_artifact_hash": preview.content_hash,
            "disposable_workspace_only": True,
            "original_case_unchanged": _directory_hash(case_dir) == source_snapshot,
            "advisory_only": True,
            "authority_granted": False,
            "duration_ms": round((time.monotonic() - started) * 1_000),
        }
        if not result["original_case_unchanged"]:
            raise LocalSynthesisEvaluationError(
                "source_case_changed",
                "The original benchmark case changed during evaluation.",
            )
        return result


def _prepare_evaluation(
    *,
    root: Path,
    workspace: Path,
    metadata: dict[str, Any],
    initial_test: dict[str, Any],
    gateway,
) -> PreparedEvaluation:
    database = root / "astra-evaluation.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()

    files = _workspace_files(workspace)
    file_hashes = {
        path: _sha256(workspace / path)
        for path in files
    }
    manifest_entries = [
        {"path": path, "sha256": file_hashes[path]}
        for path in files
    ]
    manifest_hash = content_hash(manifest_entries)
    repository_root_fingerprint = content_hash({
        "case_id": metadata["case_id"],
        "files": manifest_entries,
    })
    expected_source_file = _safe_relative_path(
        str(metadata.get("expected_source_file") or "")
    )
    allowed_modify_paths = (
        (expected_source_file,)
        if (
            expected_source_file in files
            and expected_source_file.endswith(".py")
            and expected_source_file
            != str(metadata["expected_test_file"]).replace("\\", "/")
        )
        else tuple(
            path
            for path in files
            if path.endswith(".py")
            and path != str(metadata["expected_test_file"]).replace("\\", "/")
        )
    )
    if not allowed_modify_paths:
        raise LocalSynthesisEvaluationError(
            "no_candidate_files",
            "The selected case has no bounded implementation candidates.",
        )

    project = CanonicalProjectService(control, artifacts).create_project(
        conversation_id=f"evaluation:{metadata['case_id']}",
        workspace_id=f"evaluation-workspace:{metadata['case_id']}",
        repository_root=workspace,
        repository_root_fingerprint=repository_root_fingerprint,
        actor_id="local-evaluation-user",
        idempotency_key=f"create-evaluation:{metadata['case_id']}",
        folder_authority={
            "status": "completed",
            "action_id": f"evaluation-workspace:{metadata['case_id']}",
            "conversation_id": f"evaluation:{metadata['case_id']}",
            "workspace_id": f"evaluation-workspace:{metadata['case_id']}",
            "repository_root": str(workspace),
            "repository_root_fingerprint": repository_root_fingerprint,
        },
        specification={
            "specification_hash": content_hash({
                "case_id": metadata["case_id"],
                "goal": metadata["goal"],
            }),
            "included_paths": list(files),
            "allowed_operations": ["read", "approved_patch", "verification"],
        },
        manifest={
            "manifest_hash": manifest_hash,
            "complete": True,
            "entries": manifest_entries,
        },
        plan={
            "acceptance_criteria": [{
                "criterion_id": "evaluation-focused-test",
                "required": True,
                "verification_mode": "existing_test",
                "test_path": metadata["expected_test_file"],
            }],
            "work_units": [{
                "work_unit_id": "evaluation-work-unit",
                "summary": str(metadata["goal"]),
                "expected_files": list(allowed_modify_paths),
                "acceptance_criteria_ids": ["evaluation-focused-test"],
            }],
        },
    )
    run = control.get_project(project.project_run_id)
    plan_artifact = artifacts.get(run.current_artifact_ids["plan"])
    if plan_artifact is None:
        raise LocalSynthesisEvaluationError(
            "missing_plan",
            "The temporary canonical project has no plan artifact.",
        )
    control.execute(ProjectCommand(
        command_type=ProjectCommandType.APPROVE_PLAN,
        project_run_id=run.project_run_id,
        conversation_id=run.conversation_id,
        workspace_id=run.workspace_id,
        repository_root=run.repository_root,
        repository_root_fingerprint=run.repository_root_fingerprint,
        actor_id=run.actor_id,
        expected_state_version=run.state_version,
        idempotency_key=f"approve-evaluation:{metadata['case_id']}",
        plan_revision_id=run.current_plan_revision_id,
        scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash,
        authority_scope={"operation": "prepare_work_units"},
        artifact_id=plan_artifact.artifact_id,
        artifact_type=plan_artifact.artifact_type.value,
        artifact_hash=plan_artifact.content_hash,
        artifact_binding_hash=plan_artifact.binding_hash,
    ))
    coordinator = ProjectCoordinatorService(database, control)
    coordinator.initialize()
    intent = coordinator.reconcile(project.project_run_id)
    if intent is None:
        raise LocalSynthesisEvaluationError(
            "missing_intent",
            "The temporary project produced no work-unit intent.",
        )

    retrieval = ProjectRetrievalService(database, control, artifacts)
    retrieval.initialize()
    current = control.get_project(project.project_run_id)
    scope = control.get_scope_revision(current.current_scope_revision_id)
    plan = control.get_plan_revision(current.current_plan_revision_id)
    repository_state_hash = retrieval.compute_repository_state(
        workspace,
        scope.included_paths,
        scope.excluded_paths,
    )
    binding = {
        "project_id": current.project_run_id,
        "conversation_id": current.conversation_id,
        "actor_id": current.actor_id,
        "workspace_id": current.workspace_id,
        "repository_root": current.repository_root,
        "scope_revision_id": scope.scope_revision_id,
        "scope_hash": scope.content_hash,
        "plan_revision_id": plan.plan_revision_id,
        "plan_hash": plan.content_hash,
        "repository_manifest_hash": current.current_manifest_hash,
        "repository_state_hash": repository_state_hash,
        "expected_project_state_version": current.state_version,
        "authority_id": canonical_retrieval_authority_id(current),
    }
    retrieval.ingest_project_corpus(CorpusIngestionRequest(
        **binding,
        idempotency_key=f"ingest-evaluation:{metadata['case_id']}",
    ))
    query = (
        f"{metadata['goal']}\n"
        f"Failing test: {metadata['expected_test_file']}\n"
        f"{initial_test.get('output_tail') or ''}"
    )[:4_000]
    normalized = normalize_query(query)
    retrieval_request = RetrievalRequest(
        **binding,
        request_id=f"evaluation-retrieval:{metadata['case_id']}",
        query=query,
        normalized_query=normalized,
        query_hash=content_hash(normalized),
        idempotency_key=f"evaluation-retrieval:{metadata['case_id']}",
        max_candidates=30,
        max_rerank=12,
        max_evidence=3,
        created_at=datetime.now(timezone.utc),
    )
    retrieval_artifact = retrieval.retrieve(retrieval_request)
    retrieval_evidence = retrieval.phase5b_evidence(
        retrieval_artifact.artifact_id,
        retrieval_request,
    )
    evidence = {
        "project_run_id": intent.project_run_id,
        "workspace_id": current.workspace_id,
        "repository_state_hash": repository_state_hash,
        "allowed_modify_paths": list(allowed_modify_paths),
        "allowed_create_paths": [],
        "allowed_delete_paths": [],
        "work_unit": {
            "work_unit_id": "evaluation-work-unit",
            "summary": str(metadata["goal"]),
            "expected_files": list(allowed_modify_paths),
        },
        "failure_evidence": {
            "status": initial_test["status"],
            "failing_tests": initial_test["failing_tests"],
            "assertions": initial_test["assertions"],
            "stack_source_paths": initial_test["stack_source_paths"],
            "error_types": initial_test["error_types"],
            "output_tail": initial_test["output_tail"],
        },
        "file_identities": file_hashes,
        "source_excerpts": [
            {
                "path": path,
                "sha256": file_hashes[path],
                "text": (workspace / path).read_text(encoding="utf-8"),
            }
            for path in allowed_modify_paths
            if (workspace / path).stat().st_size <= 24_000
        ],
        "project_rag": {
            "status": "attached",
            "retrieval_artifact_id": retrieval_artifact.artifact_id,
            "retrieval_artifact_hash": retrieval_artifact.artifact_hash,
            "evidence_count": retrieval_artifact.evidence_count,
            "advisory_only": True,
        },
    }
    evidence_artifact = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.COORDINATOR_DECISION,
        binding=ProjectArtifactBinding(
            project_run_id=intent.project_run_id,
            plan_revision_id=intent.plan_revision_id,
            scope_revision_id=intent.scope_revision_id,
            manifest_hash=intent.manifest_hash,
            coordinator_intent_id=intent.coordinator_intent_id,
        ),
        payload={"evidence": evidence},
        evidence_references=({
            "artifact_id": retrieval_artifact.artifact_id,
            "artifact_type": ProjectArtifactType.RETRIEVAL_EVIDENCE.value,
            "content_hash": retrieval_artifact.artifact_hash,
        },),
    ))
    invocations = ProjectModelInvocationStore(database)
    invocations.initialize()
    orchestrator = CanonicalSynthesisOrchestrator(
        invocations=invocations,
        artifacts=artifacts,
        gateway=gateway,
        control=control,
    )
    return PreparedEvaluation(
        orchestrator=orchestrator,
        artifacts=artifacts,
        intent=intent,
        evidence_artifact=evidence_artifact,
        retrieval_evidence=retrieval_evidence,
        allowed_modify_paths=allowed_modify_paths,
        file_hashes=file_hashes,
        retrieval_summary={
            "artifact_id": retrieval_artifact.artifact_id,
            "artifact_hash": retrieval_artifact.artifact_hash,
            "evidence_count": retrieval_artifact.evidence_count,
            "context_chars": sum(
                len(item.text) for item in retrieval_artifact.evidence
            ),
            "paths": [
                item.relative_path for item in retrieval_artifact.evidence
            ],
            "advisory_only": True,
        },
    )


def _apply_disposable_operations(
    workspace: Path,
    operations: tuple[dict[str, Any], ...],
    *,
    allowed_modify_paths: set[str],
) -> dict[str, Any]:
    if not operations:
        raise LocalSynthesisEvaluationError(
            "empty_patch",
            "The synthesis preview contains no operations.",
        )
    changed_paths: list[str] = []
    changed_lines = 0
    unexpected_paths: list[str] = []
    for operation in operations:
        path = _safe_relative_path(str(operation.get("path") or ""))
        if path not in allowed_modify_paths:
            unexpected_paths.append(path)
            continue
        if operation.get("operation") != "modify":
            raise LocalSynthesisEvaluationError(
                "unsupported_operation",
                "The bounded repair evaluator accepts modify operations only.",
            )
        target = _resolve_workspace_path(workspace, path)
        before = target.read_text(encoding="utf-8")
        if _sha256(target) != operation.get("expected_sha256"):
            raise LocalSynthesisEvaluationError(
                "stale_before_hash",
                "The synthesis operation does not match current disposable bytes.",
            )
        strategy = operation.get("strategy")
        if strategy == "complete_content":
            after = operation.get("content")
            if not isinstance(after, str):
                raise LocalSynthesisEvaluationError(
                    "invalid_operation_content",
                    "The synthesis operation contains no complete content.",
                )
        elif strategy == "exact_replacements":
            after = _apply_exact_replacements(
                before,
                tuple(operation.get("replacements") or ()),
            )
        else:
            raise LocalSynthesisEvaluationError(
                "unsupported_patch_strategy",
                "The synthesis operation uses an unsupported patch strategy.",
            )
        if after == before:
            raise LocalSynthesisEvaluationError(
                "no_effect_patch",
                "The synthesis operation does not change the selected file.",
            )
        if target.suffix == ".py":
            try:
                compile(after, path, "exec")
            except SyntaxError as exc:
                raise LocalSynthesisEvaluationError(
                    "syntax_invalid",
                    "The synthesized Python content does not compile.",
                    diagnostic={
                        "syntax_error_type": type(exc).__name__,
                        "syntax_error_line": exc.lineno,
                        "syntax_error_offset": exc.offset,
                        "syntax_error_reason": " ".join(
                            str(exc.msg or "invalid syntax").split()
                        )[:200],
                    },
                ) from exc
        target.write_text(after, encoding="utf-8")
        changed_paths.append(path)
        changed_lines += _changed_line_count(before, after)
    if unexpected_paths:
        raise LocalSynthesisEvaluationError(
            "unexpected_patch_path",
            "The synthesis preview contains a path outside evaluation scope.",
        )
    return {
        "changed_paths": changed_paths,
        "unexpected_paths": unexpected_paths,
        "changed_line_count": changed_lines,
        "syntax_valid": True,
    }


def _apply_exact_replacements(
    before: str,
    replacements: tuple[dict[str, Any], ...],
) -> str:
    lines = before.splitlines(keepends=True)
    ordered = sorted(
        replacements,
        key=lambda item: (int(item["start_line"]), int(item["end_line"])),
        reverse=True,
    )
    previous_start = len(lines) + 1
    for replacement in ordered:
        start = int(replacement["start_line"])
        end = int(replacement["end_line"])
        if start < 1 or end < start or end >= previous_start or end > len(lines):
            raise LocalSynthesisEvaluationError(
                "invalid_replacement_range",
                "The synthesis replacement range is invalid or overlapping.",
            )
        actual = "".join(lines[start - 1:end])
        if actual != replacement.get("expected_text"):
            expected = replacement.get("expected_text")
            expected_text = expected if isinstance(expected, str) else ""
            raise LocalSynthesisEvaluationError(
                "replacement_anchor_mismatch",
                "The synthesis replacement anchor does not match current bytes.",
                diagnostic={
                    "replacement_start_line": start,
                    "replacement_end_line": end,
                    "actual_text_hash": hashlib.sha256(
                        actual.encode("utf-8")
                    ).hexdigest(),
                    "expected_text_hash": hashlib.sha256(
                        expected_text.encode("utf-8")
                    ).hexdigest(),
                    "actual_ends_with_newline": actual.endswith("\n"),
                    "expected_ends_with_newline": expected_text.endswith("\n"),
                },
            )
        lines[start - 1:end] = [str(replacement.get("replacement_text") or "")]
        previous_start = start
    return "".join(lines)


def _run_focused_pytest(
    workspace: Path,
    test_path: str,
    *,
    python_executable: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [python_executable, "-m", "pytest", "-q", test_path],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=_test_environment(),
        )
        output = (completed.stdout + completed.stderr).strip()
        return parse_pytest_output(
            output,
            exit_code=completed.returncode,
            max_tail=MAX_REPORT_OUTPUT_TAIL,
        )
    except subprocess.TimeoutExpired:
        return {
            **parse_pytest_output("", exit_code=124),
            "status": "timed_out",
            "output_tail": "Focused pytest exceeded its bounded timeout.",
        }


def _generation_diagnostic(
    orchestrator: CanonicalSynthesisOrchestrator,
    outcome,
    gateway,
) -> dict[str, Any]:
    if not outcome.proposal_id:
        return {}
    proposal = orchestrator.proposals.get(outcome.proposal_id)
    service = getattr(gateway, "local_ai_service", None)
    if proposal is None or service is None:
        return {}
    diagnostic = service.generation_diagnostic(proposal.generation_id)
    allowed = {
        "generation_failure_classification",
        "provider_identity",
        "exact_model_tag",
        "response_schema_identity",
        "response_schema_hash",
        "duration_ms",
        "prompt_eval_count",
        "eval_count",
    }
    return {
        key: diagnostic.get(key)
        for key in sorted(allowed)
    }


def resolve_case(suite: str, case_id: str) -> Path:
    if suite not in SUITES or not CASE_ID_PATTERN.fullmatch(case_id):
        raise LocalSynthesisEvaluationError(
            "invalid_case_identity",
            "The requested benchmark case identity is invalid.",
        )
    suite_root = SUITES[suite].resolve(strict=True)
    candidate = (suite_root / case_id).resolve(strict=True)
    if suite_root not in candidate.parents or not candidate.is_dir():
        raise LocalSynthesisEvaluationError(
            "invalid_case_identity",
            "The requested benchmark case is outside the selected suite.",
        )
    metadata = _load_metadata(candidate)
    if metadata["case_id"] != case_id:
        raise LocalSynthesisEvaluationError(
            "invalid_case_identity",
            "The benchmark directory and metadata identities differ.",
        )
    return candidate


def _load_metadata(case_dir: Path) -> dict[str, Any]:
    try:
        metadata = json.loads(
            (case_dir / "metadata.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalSynthesisEvaluationError(
            "invalid_case_metadata",
            "The selected benchmark metadata is invalid.",
        ) from exc
    required = ("case_id", "goal", "expected_source_file", "expected_test_file")
    if not isinstance(metadata, dict) or any(
        not isinstance(metadata.get(key), str) or not metadata[key]
        for key in required
    ):
        raise LocalSynthesisEvaluationError(
            "invalid_case_metadata",
            "The selected benchmark metadata is incomplete.",
        )
    return metadata


def _workspace_files(workspace: Path) -> tuple[str, ...]:
    root = workspace.resolve()
    paths = []
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if root not in resolved.parents:
            raise LocalSynthesisEvaluationError(
                "workspace_escape",
                "The disposable workspace contains an escaping file.",
            )
        relative = resolved.relative_to(root).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            continue
        paths.append(relative)
    return tuple(sorted(paths))


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != normalized
    ):
        raise LocalSynthesisEvaluationError(
            "invalid_relative_path",
            "The evaluation contains an unsafe relative path.",
        )
    return normalized


def _resolve_workspace_path(workspace: Path, relative_path: str) -> Path:
    root = workspace.resolve()
    target = root.joinpath(*PurePosixPath(relative_path).parts).resolve()
    if root not in target.parents or not target.is_file():
        raise LocalSynthesisEvaluationError(
            "invalid_patch_target",
            "The synthesis patch target is missing or outside the workspace.",
        )
    return target


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_hash(root: Path) -> str:
    material = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in sorted(
            (item for item in root.rglob("*") if item.is_file()),
            key=lambda item: item.as_posix(),
        )
    ]
    return content_hash(material)


def _changed_line_count(before: str, after: str) -> int:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    common = sum(
        left == right
        for left, right in zip(before_lines, after_lines)
    )
    return (len(before_lines) - common) + (len(after_lines) - common)


def _test_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["TMP"] = "/tmp"
    environment["TEMP"] = "/tmp"
    environment["TMPDIR"] = "/tmp"
    return environment


def _write_report(report: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).expanduser().resolve().write_text(
            payload,
            encoding="utf-8",
        )
    print(payload, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one explicitly confirmed canonical local-synthesis repair "
            "evaluation in a disposable benchmark copy."
        )
    )
    parser.add_argument("--suite", choices=sorted(SUITES), default="real")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output")
    parser.add_argument(
        "--confirm-advisory-generation",
        action="store_true",
    )
    parser.add_argument(
        "--confirm-disposable-apply-and-test",
        action="store_true",
    )
    arguments = parser.parse_args(argv)
    if (
        not arguments.confirm_advisory_generation
        or not arguments.confirm_disposable_apply_and_test
    ):
        _write_report({
            "schema_version": "astra.local-synthesis.case-evaluation.v1",
            "status": "confirmation_required",
            "failure_classification": "confirmation_required",
            "advisory_only": True,
            "authority_granted": False,
        }, arguments.output)
        return 2
    try:
        case_dir = resolve_case(arguments.suite, arguments.case_id)
        report = evaluate_case(case_dir)
    except LocalSynthesisEvaluationError as exc:
        _write_report({
            "schema_version": "astra.local-synthesis.case-evaluation.v1",
            "status": "failed",
            "failure_classification": exc.code,
            "reason": " ".join(str(exc).split())[:300],
            "diagnostic": {
                key: value
                for key, value in exc.diagnostic.items()
                if key in {
                    "syntax_error_type",
                    "syntax_error_line",
                    "syntax_error_offset",
                    "syntax_error_reason",
                    "replacement_start_line",
                    "replacement_end_line",
                    "actual_text_hash",
                    "expected_text_hash",
                    "actual_ends_with_newline",
                    "expected_ends_with_newline",
                }
            },
            "advisory_only": True,
            "authority_granted": False,
        }, arguments.output)
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "evaluation_failed")
        diagnostic = getattr(exc, "diagnostic", {})
        _write_report({
            "schema_version": "astra.local-synthesis.case-evaluation.v1",
            "status": "failed",
            "failure_classification": str(code)[:120],
            "reason": "The bounded local synthesis evaluation failed.",
            "diagnostic": {
                key: diagnostic.get(key)
                for key in (
                    "provider_reachable",
                    "configured_model_missing",
                    "provider_readiness_reason",
                    "provider_identity",
                    "exact_model_tag",
                    "admission_outcome",
                    "estimated_required_bytes",
                    "available_bytes",
                    "safety_reserve_bytes",
                    "admission_backend",
                    "admission_device",
                    "admitted_context",
                    "validation_error_location",
                    "validation_error_type",
                    "validation_error_reason",
                    "response_schema_identity",
                    "response_schema_hash",
                    "duration_ms",
                    "prompt_eval_count",
                    "eval_count",
                )
                if key in diagnostic
            },
            "error_type": type(exc).__name__[:120],
            "advisory_only": True,
            "authority_granted": False,
        }, arguments.output)
        return 2
    _write_report(report, arguments.output)
    return 0 if report["verified_repair_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
