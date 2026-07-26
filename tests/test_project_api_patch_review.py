from __future__ import annotations

from backend.app.project_api.routes import _summary, build_canonical_project_response
from backend.app.project_artifacts import (
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control.project_service import CanonicalProjectService
from tests.test_project_coordinator_synthesis import _executor_with_synthesis


def test_project_response_exposes_only_exact_current_patch_for_review(
    tmp_path,
) -> None:
    control, artifacts, coordinator, executor, project_id, _gateway, _invocations = (
        _executor_with_synthesis(tmp_path)
    )
    assert executor.run_once("coordinator-worker") is True
    current = artifacts.list_for_project(
        project_id,
        artifact_type=ProjectArtifactType.PATCH_PREVIEW,
    )[0]
    historical = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.PATCH_PREVIEW,
        binding=current.binding,
        payload={
            "patch_id": "historical-patch",
            "operations": [{
                "operation": "modify",
                "path": "src/app.py",
                "expected_sha256": "4" * 64,
                "strategy": "complete_content",
                "content": "VALUE = 99\n",
                "rationale": "Historical proposal that is not current.",
                "affected_symbols": ["VALUE"],
                "evidence_references": ["src/app.py"],
            }],
            "requires_exact_approval": True,
        },
    ))
    service = CanonicalProjectService(control, artifacts)
    artifact_count = len(service.list_artifacts(project_id))
    event_count = len(control.list_events(project_id))

    response = build_canonical_project_response(
        service,
        project_id,
        coordinator=coordinator,
    )

    action = next(
        item
        for item in response.next_permitted_actions
        if item.action == "approve_patch"
    )
    assert action.artifact_id == current.artifact_id
    summaries = {item.artifact_id: item for item in response.artifacts}
    review = summaries[current.artifact_id].patch_review
    assert review is not None
    assert review.review_complete is True
    assert review.requires_exact_approval is True
    assert review.advisory_only is True
    assert review.operation_count == 1
    assert review.operations[0].path == "src/app.py"
    assert review.operations[0].content == "VALUE = 2\n"
    assert review.operations[0].rationale == (
        "Implements the approved requirement."
    )
    assert summaries[historical.artifact_id].patch_review is None
    assert len(service.list_artifacts(project_id)) == artifact_count
    assert len(control.list_events(project_id)) == event_count


def test_artifact_collection_summary_does_not_embed_patch_content(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id, _gateway, _invocations = (
        _executor_with_synthesis(tmp_path)
    )
    assert executor.run_once("coordinator-worker") is True
    service = CanonicalProjectService(control, artifacts)

    response = build_canonical_project_response(
        service,
        project_id,
        coordinator=coordinator,
    )
    current = next(
        item
        for item in response.artifacts
        if item.patch_review is not None
    )
    metadata_only = next(
        item
        for item in (
            # The artifact listing route calls the same summary helper without
            # opting into current-action review content.
            _summary(artifact)
            for artifact in artifacts.list_for_project(project_id)
        )
        if item.artifact_id == current.artifact_id
    )

    assert metadata_only.patch_review is None


def test_truncated_or_incomplete_operation_never_claims_exact_review(
    tmp_path,
) -> None:
    control, artifacts, _coordinator, _executor, project_id, _gateway, _invocations = (
        _executor_with_synthesis(tmp_path)
    )
    run = control.get_project(project_id)
    plan_artifact = artifacts.get(run.current_artifact_ids["plan"])
    assert plan_artifact is not None
    preview = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.PATCH_PREVIEW,
        binding=plan_artifact.binding,
        payload={
            "operations": [{
                "operation": "modify",
                "path": "src/app.py",
                "expected_sha256": "3" * 64,
                "strategy": "complete_content",
                "content": "VALUE = 2\n",
                "rationale": "x" * 2001,
            }],
            "requires_exact_approval": True,
        },
    ))

    review = _summary(preview, include_patch_review=True).patch_review

    assert review is not None
    assert review.review_complete is False
    assert review.operations[0].rationale == "x" * 2000
