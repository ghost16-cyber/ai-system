from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.project_validation.acceptance import evaluate_acceptance_criteria
from backend.app.project_validation.contracts import (
    AcceptanceResult,
    BudgetUsage,
    HumanReviewAction,
    ValidationCampaign,
    ValidationLimits,
    ValidationState,
)
from backend.app.project_validation.inspection import build_deliverable_manifest
from backend.app.project_validation.limits import BudgetExceededError, add_usage, enforce_budget, evaluate_budget
from backend.app.project_validation.quality import assess_quality
from backend.app.project_validation.regression import evaluate_regression
from backend.app.project_validation.scenarios import get_scenario, list_scenarios
from backend.app.project_validation.service import ProjectValidationError, ProjectValidationService
from backend.app.project_validation.store import ProjectValidationStore
from backend.app.project_validation.workflow import transition_state
from backend.app.project_validation.workspace import (
    WorkspaceSecurityError,
    capture_snapshot,
    compare_snapshot,
    prepare_workspace,
    restore_snapshot,
)


def _scope() -> tuple[dict, str, str]:
    scope = {
        "engagement_title": "Findings report",
        "problem_statement": "Produce a verified findings report.",
        "desired_outcome": "A reproducible findings report exists and passes validation.",
        "deliverables": [{
            "deliverable_id": "report",
            "title": "Findings report",
            "description": "A Markdown findings report.",
            "acceptance_criteria": [{
                "criterion_id": "criterion-report",
                "deliverable_id": "report",
                "statement": "The findings report exists and its verification check passes.",
                "review_mode": "automated",
                "evidence_ids": ["evidence-1"],
            }],
            "evidence_ids": ["evidence-1"],
        }],
        "exclusions": [],
    }
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return scope, canonical, digest


def _engagement() -> dict:
    scope, canonical, digest = _scope()
    return {
        "engagement_id": "engagement-1",
        "conversation_id": "conversation-1",
        "state": "project_launched",
        "approved_scope_revision_id": "revision-1",
        "current_scope_revision": {
            "revision_id": "revision-1",
            "revision_number": 1,
            "scope_hash": digest,
            "canonical_scope": canonical,
            "scope": scope,
        },
        "project_launch": {"delivery_job_id": "delivery-1"},
    }


def _delivery(status: str = "delivery_completed") -> dict:
    digest = _engagement()["current_scope_revision"]["scope_hash"]
    return {
        "delivery_job_id": "delivery-1",
        "conversation_id": "conversation-1",
        "status": status,
        "plan": {"plan_revision": 1, "plan_hash": "a" * 64, "work_units": [{"expected_files": ["report.md"]}]},
        "client_engagement": {
            "engagement_id": "engagement-1",
            "scope_revision_id": "revision-1",
            "scope_hash": digest,
        },
    }


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "report.md").write_text("# Findings\n\nVerified result.\n", encoding="utf-8")
    return root


def test_contracts_are_strict_and_versioned(tmp_path: Path) -> None:
    service = ProjectValidationService(tmp_path / "validation.db")
    project = _project(tmp_path)
    campaign = service.create_campaign(
        engagement=_engagement(), delivery=_delivery(), conversation_id="conversation-1",
        user_id="user", authorization_id="access-1", workspace_root=project,
    )
    payload = campaign.model_dump(mode="json")
    payload["unknown"] = True
    with pytest.raises(ValidationError):
        ValidationCampaign.model_validate(payload)
    payload.pop("unknown")
    payload["schema_version"] = "astra.project-validation.v2"
    with pytest.raises(ValidationError):
        ValidationCampaign.model_validate(payload)


def test_state_machine_rejects_invalid_transition() -> None:
    assert transition_state(ValidationState.CREATED, ValidationState.PREPARING_WORKSPACE) == ValidationState.PREPARING_WORKSPACE
    with pytest.raises(ValueError, match="Invalid validation transition"):
        transition_state(ValidationState.CREATED, ValidationState.DELIVERY_READY)


def test_workspace_requires_authorization_and_rejects_escape_symlink(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(WorkspaceSecurityError):
        prepare_workspace(project, authorization_id="", conversation_id="conversation-1")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (project / "escape.txt").symlink_to(outside)
    workspace = prepare_workspace(project, authorization_id="access", conversation_id="conversation")
    with pytest.raises(WorkspaceSecurityError, match="symbolic link|outside"):
        capture_snapshot(workspace, campaign_id="campaign", limits=ValidationLimits())


def test_snapshot_detects_created_modified_and_deleted_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "old.txt").write_text("old", encoding="utf-8")
    workspace = prepare_workspace(project, authorization_id="access", conversation_id="conversation")
    baseline = capture_snapshot(workspace, campaign_id="campaign", limits=ValidationLimits())
    (project / "report.md").write_text("changed", encoding="utf-8")
    (project / "old.txt").unlink()
    (project / "new.txt").write_text("new", encoding="utf-8")
    diff = compare_snapshot(baseline, workspace, ValidationLimits())
    assert diff["stale"] is True
    assert diff["created"] == ["new.txt"]
    assert diff["deleted"] == ["old.txt"]
    assert diff["modified"] == ["report.md"]


def test_snapshot_restore_recovers_modified_deleted_and_created_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "old.txt").write_text("old", encoding="utf-8")
    workspace = prepare_workspace(project, authorization_id="access", conversation_id="conversation")
    baseline = capture_snapshot(workspace, campaign_id="campaign", limits=ValidationLimits())
    (project / "report.md").write_text("changed", encoding="utf-8")
    (project / "old.txt").unlink()
    (project / "created.txt").write_text("created", encoding="utf-8")
    restored = restore_snapshot(baseline, workspace)
    assert restored["complete"] is True
    assert (project / "report.md").read_text(encoding="utf-8").startswith("# Findings")
    assert (project / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (project / "created.txt").exists()


def test_pause_resume_cancel_and_scope_invalidation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = ProjectValidationService(tmp_path / "validation.db")
    campaign = service.create_campaign(
        engagement=_engagement(), delivery=_delivery(), conversation_id="conversation-1",
        user_id="user", authorization_id="access", workspace_root=project,
    )
    campaign = service.prepare(campaign_id=campaign.campaign_id, expected_version=campaign.state_version, authorization_id="access", workspace_root=project, actor_id="user")
    campaign, run = service.start_run(campaign_id=campaign.campaign_id, expected_version=campaign.state_version, actor_id="user")
    campaign, run = service.pause(
        campaign_id=campaign.campaign_id, run_id=run.run_id, expected_campaign_version=campaign.state_version,
        expected_run_version=run.state_version, actor_id="user",
    )
    assert campaign.state == ValidationState.EXECUTION_PAUSED
    changed = _engagement()
    changed["approved_scope_revision_id"] = "revision-2"
    with pytest.raises(ProjectValidationError) as invalidated:
        service.resume(
            campaign_id=campaign.campaign_id, run_id=run.run_id, expected_campaign_version=campaign.state_version,
            expected_run_version=run.state_version, actor_id="user", current_engagement=changed, current_delivery=_delivery(),
        )
    assert invalidated.value.code == "scope_invalidated"
    campaign, run = service.resume(
        campaign_id=campaign.campaign_id, run_id=run.run_id, expected_campaign_version=campaign.state_version,
        expected_run_version=run.state_version, actor_id="user", current_engagement=_engagement(), current_delivery=_delivery(),
    )
    campaign, run = service.cancel(
        campaign_id=campaign.campaign_id, expected_campaign_version=campaign.state_version,
        expected_run_version=run.state_version, actor_id="user", reason="No longer needed.",
    )
    assert campaign.state == ValidationState.CANCELLED
    assert run and run.state == ValidationState.CANCELLED


def test_budget_warning_and_exceeded_behavior() -> None:
    limits = ValidationLimits(max_command_executions=10, max_modified_files=2)
    warning = evaluate_budget(limits, BudgetUsage(command_executions=8))
    assert "command_executions" in warning.warnings
    usage = add_usage(BudgetUsage(), modified_files=3)
    exceeded = evaluate_budget(limits, usage)
    assert exceeded.exceeded == ("modified_files",)
    with pytest.raises(BudgetExceededError):
        enforce_budget(limits, usage)


def test_acceptance_requires_evidence_and_never_auto_passes_human_review() -> None:
    criteria = [
        {"criterion_id": "a", "statement": "Tests pass", "review_mode": "automated", "required": True},
        {"criterion_id": "b", "statement": "The layout is visually acceptable", "review_mode": "human_review_required", "required": True},
    ]
    values = evaluate_acceptance_criteria(criteria, {"a": [{"result": "passed", "summary": "Tests passed", "deterministic": True}]})
    assert values[0].result == AcceptanceResult.PASSED
    assert values[1].result == AcceptanceResult.REQUIRES_HUMAN_REVIEW
    assert values[1].human_review_required is True
    missing = evaluate_acceptance_criteria([criteria[0]], {})[0]
    assert missing.result == AcceptanceResult.BLOCKED
    assert missing.blocking is True


def test_failed_deterministic_evidence_cannot_be_overridden() -> None:
    criterion = {"criterion_id": "a", "statement": "Build succeeds", "review_mode": "approved_command", "required": True}
    value = evaluate_acceptance_criteria([criterion], {"a": [
        {"result": "failed", "summary": "Build failed", "deterministic": True},
        {"result": "passed", "summary": "Model says it looks fine", "deterministic": False},
    ]})[0]
    assert value.result == AcceptanceResult.FAILED
    assert value.blocking is True


def test_human_review_criterion_still_blocks_on_deterministic_failure() -> None:
    criterion = {"criterion_id": "visual", "statement": "Responsive layout is visually acceptable", "review_mode": "human_review_required", "required": True}
    value = evaluate_acceptance_criteria([criterion], {"visual": [{
        "result": "failed", "summary": "Responsive smoke check failed", "deterministic": True,
    }]})[0]
    assert value.result == AcceptanceResult.FAILED
    assert value.blocking is True


def test_four_chart_deliverable_requires_four_distinct_artifacts(tmp_path: Path) -> None:
    project = tmp_path / "charts"
    project.mkdir()
    for name in ("pie.png", "bar.png", "scatter.png"):
        (project / name).write_bytes(name.encode("utf-8"))
    deliverable = [{
        "deliverable_id": "charts", "title": "Four analytical charts",
        "description": "Exactly four charts from the approved dataset.", "acceptance_criteria": [],
    }]
    missing = build_deliverable_manifest(run_id="run", workspace_root=project, deliverables=deliverable)
    assert missing.complete is False
    assert len([item for item in missing.artifacts if item.exists]) == 3
    (project / "histogram.png").write_bytes(b"histogram")
    complete = build_deliverable_manifest(run_id="run-2", workspace_root=project, deliverables=deliverable)
    assert complete.complete is True
    assert len(complete.artifacts) == 4
    assert len({item.relative_path for item in complete.artifacts}) == 4


def test_manifest_rejects_escaping_hint_and_reports_missing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    deliverables = [{"deliverable_id": "report", "title": "Findings report", "description": "Markdown report", "acceptance_criteria": []}]
    manifest = build_deliverable_manifest(run_id="run", workspace_root=project, deliverables=deliverables)
    assert manifest.complete is False
    assert manifest.missing_deliverable_ids == ["report"]
    with pytest.raises(ValueError, match="escaped"):
        build_deliverable_manifest(run_id="run", workspace_root=project, deliverables=deliverables, artifact_hints={"report": "../outside.md"})


def test_regression_blocks_unplanned_change_and_regressed_test() -> None:
    result = evaluate_regression(
        run_id="run", snapshot_diff={"created": ["src/new.py"], "modified": ["global.css"], "deleted": [], "changed_bytes": 50},
        allowed_paths=["src"], regressed_tests=["test_existing_feature"],
    )
    assert result.blocking is True
    assert result.unexpected_changes == ["global.css"]
    assert result.tests_regressed == ["test_existing_feature"]


def test_quality_score_cannot_hide_blocking_failure(tmp_path: Path) -> None:
    project = _project(tmp_path)
    manifest = build_deliverable_manifest(
        run_id="run", workspace_root=project,
        deliverables=[{"deliverable_id": "report", "title": "Findings report", "description": "Markdown report", "acceptance_criteria": []}],
    )
    evaluations = evaluate_acceptance_criteria(
        [{"criterion_id": "a", "statement": "Tests pass", "review_mode": "automated", "required": True}],
        {"a": [{"result": "failed", "summary": "One test failed", "deterministic": True}]},
    )
    regression = evaluate_regression(run_id="run", snapshot_diff={"created": [], "modified": [], "deleted": [], "changed_bytes": 0})
    quality = assess_quality(run_id="run", evaluations=evaluations, manifest=manifest, regression=regression)
    assert quality.blocking_findings
    assert quality.automated_decision.value == "remediation_required"


def test_end_to_end_validation_requires_exact_human_review(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = ProjectValidationService(tmp_path / "validation.db")
    campaign = service.create_campaign(
        engagement=_engagement(), delivery=_delivery(), conversation_id="conversation-1",
        user_id="user", authorization_id="access-1", workspace_root=project,
        idempotency_key="campaign-key",
    )
    replay = service.create_campaign(
        engagement=_engagement(), delivery=_delivery(), conversation_id="conversation-1",
        user_id="user", authorization_id="access-1", workspace_root=project,
        idempotency_key="campaign-key",
    )
    assert replay.campaign_id == campaign.campaign_id
    campaign = service.prepare(
        campaign_id=campaign.campaign_id, expected_version=campaign.state_version,
        authorization_id="access-1", workspace_root=project, actor_id="user",
    )
    campaign, run = service.start_run(
        campaign_id=campaign.campaign_id, expected_version=campaign.state_version,
        actor_id="user", idempotency_key="run-key",
    )
    campaign, run = service.evaluate_run(
        campaign_id=campaign.campaign_id, run_id=run.run_id,
        expected_campaign_version=campaign.state_version, expected_run_version=run.state_version,
        actor_id="user", current_delivery=_delivery(), current_engagement=_engagement(), allowed_paths=["report.md"],
        artifact_hints={"report": "report.md"}, evidence_by_criterion={
            "criterion-report": [{
                "evidence_id": "verification-1", "result": "passed", "summary": "Report verification passed.",
                "source_reference": "stage9-verification", "deterministic": True,
            }]
        },
    )
    assert campaign.state == ValidationState.AWAITING_HUMAN_REVIEW
    assert run.result_hash
    assert run.quality_assessment and not run.quality_assessment.blocking_findings
    with pytest.raises(ProjectValidationError) as stale:
        service.human_review(
            campaign_id=campaign.campaign_id, run_id=run.run_id,
            expected_campaign_version=campaign.state_version, expected_run_version=run.state_version,
            scope_revision_id="revision-1", scope_hash=campaign.scope_reference.scope_hash,
            validation_result_hash="0" * 64, reviewer_id="user", action=HumanReviewAction.APPROVE,
            current_engagement=_engagement(),
        )
    assert stale.value.code == "stale_validation"
    campaign, run, review = service.human_review(
        campaign_id=campaign.campaign_id, run_id=run.run_id,
        expected_campaign_version=campaign.state_version, expected_run_version=run.state_version,
        scope_revision_id="revision-1", scope_hash=campaign.scope_reference.scope_hash,
        validation_result_hash=run.result_hash, reviewer_id="user", action=HumanReviewAction.APPROVE,
        current_engagement=_engagement(),
    )
    assert campaign.state == ValidationState.DELIVERY_READY
    assert run.state == ValidationState.DELIVERY_READY
    assert review.action == HumanReviewAction.APPROVE


def test_delivery_must_be_complete_and_plan_must_be_current(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = ProjectValidationService(tmp_path / "validation.db")
    campaign = service.create_campaign(
        engagement=_engagement(), delivery=_delivery("awaiting_plan_approval"), conversation_id="conversation-1",
        user_id="user", authorization_id="access", workspace_root=project,
    )
    campaign = service.prepare(campaign_id=campaign.campaign_id, expected_version=campaign.state_version, authorization_id="access", workspace_root=project, actor_id="user")
    campaign, run = service.start_run(campaign_id=campaign.campaign_id, expected_version=campaign.state_version, actor_id="user")
    with pytest.raises(ProjectValidationError) as incomplete:
        service.evaluate_run(
            campaign_id=campaign.campaign_id, run_id=run.run_id,
            expected_campaign_version=campaign.state_version, expected_run_version=run.state_version,
            actor_id="user", current_delivery=_delivery("awaiting_plan_approval"), current_engagement=_engagement(),
        )
    assert incomplete.value.code == "delivery_incomplete"


def test_tampered_scope_is_rejected_before_persistence(tmp_path: Path) -> None:
    engagement = _engagement()
    engagement["current_scope_revision"]["scope"]["desired_outcome"] = "Tampered"
    service = ProjectValidationService(tmp_path / "validation.db")
    with pytest.raises(ProjectValidationError) as error:
        service.create_campaign(
            engagement=engagement, delivery=_delivery(), conversation_id="conversation-1",
            user_id="user", authorization_id="access", workspace_root=_project(tmp_path),
        )
    assert error.value.code == "tampered_scope"


def test_stale_version_is_rejected(tmp_path: Path) -> None:
    service = ProjectValidationService(tmp_path / "validation.db")
    project = _project(tmp_path)
    campaign = service.create_campaign(
        engagement=_engagement(), delivery=_delivery(), conversation_id="conversation-1",
        user_id="user", authorization_id="access", workspace_root=project,
    )
    with pytest.raises(ProjectValidationError) as conflict:
        service.prepare(
            campaign_id=campaign.campaign_id, expected_version=999,
            authorization_id="access", workspace_root=project, actor_id="user",
        )
    assert conflict.value.code == "conflict"


def test_recovery_pauses_interrupted_run(tmp_path: Path) -> None:
    service = ProjectValidationService(tmp_path / "validation.db")
    project = _project(tmp_path)
    campaign = service.create_campaign(
        engagement=_engagement(), delivery=_delivery(), conversation_id="conversation-1",
        user_id="user", authorization_id="access", workspace_root=project,
    )
    campaign = service.prepare(campaign_id=campaign.campaign_id, expected_version=campaign.state_version, authorization_id="access", workspace_root=project, actor_id="user")
    campaign, run = service.start_run(campaign_id=campaign.campaign_id, expected_version=campaign.state_version, actor_id="user")
    recovered = service.recover(campaign_id=campaign.campaign_id, actor_id="user")
    assert recovered.state == ValidationState.EXECUTION_PAUSED
    assert service.get_run(campaign.campaign_id, run.run_id).state == ValidationState.EXECUTION_PAUSED


def test_store_history_is_immutable_and_audit_is_redacted(tmp_path: Path) -> None:
    store = ProjectValidationStore(tmp_path / "store.db")
    store.initialize()
    event = store.audit(
        campaign_id="campaign", event_type="test", actor_id="user",
        payload={"stdout": "secret output", "token": "hidden", "summary": "safe"},
    )
    assert event.payload["stdout"] == "[redacted]"
    assert event.payload["token"] == "[redacted]"
    assert store.audit_history("campaign")[0].payload["summary"] == "safe"


def test_required_real_world_scenarios_are_present_and_deterministic() -> None:
    scenarios = list_scenarios()
    assert [item.scenario_id for item in scenarios] == sorted(item.scenario_id for item in scenarios)
    assert {item.scenario_id for item in scenarios} == {
        "restaurant-website", "csv-upload-repair", "sales-analysis",
        "searchable-products", "incomplete-brief", "material-scope-change",
    }
    assert all(item.failure_variants for item in scenarios)
    assert get_scenario("sales-analysis").expected_deliverables == [
        "pie chart", "bar chart", "scatter plot", "histogram", "findings report",
    ]
