import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.repo_scanner.llm_engine.output_schema import RecommendedAction, RepoDecision
from backend.app.repo_scanner.planner import build_execution_plan


def test_build_execution_plan_is_proposal_only():
    decision = RepoDecision(
        repo_identity="Repo scanner",
        architecture_summary="Static analyzer",
        confidence=0.8,
        risks=[],
        recommended_actions=[
            RecommendedAction(
                action_type="refactor",
                action="Refactor scanner",
                priority=2,
                target_area="backend/app/repo_scanner/scanner.py",
                requires_file_edit=True,
                rationale="Reduce complexity",
            ),
            RecommendedAction(
                action_type="inspect_module",
                action="Inspect rules",
                priority=1,
                target_area="backend/app/repo_scanner/analysis_engine/rules.py",
                requires_file_edit=False,
                rationale="Understand rule coverage",
            ),
        ],
        inspect_next=[],
        assumptions=[],
    )

    plan = build_execution_plan(decision)

    assert plan.mode == "proposal_only"
    assert [step.step_type for step in plan.steps] == ["inspect", "propose_refactor"]
    assert all(step.allowed_to_modify is False for step in plan.steps)
    assert plan.steps[0].requires_approval is False
    assert plan.steps[1].requires_approval is True
    assert all(step.target_kind == "unknown" for step in plan.steps)


def test_build_execution_plan_resolves_targets_with_scan():
    decision = RepoDecision(
        repo_identity="Repo scanner",
        architecture_summary="Static analyzer",
        confidence=0.8,
        risks=[],
        recommended_actions=[
            RecommendedAction(
                action_type="inspect_module",
                action="Inspect llm",
                priority=1,
                target_area="llm module",
                requires_file_edit=False,
                rationale="Understand model layer",
            ),
            RecommendedAction(
                action_type="inspect_file",
                action="Inspect missing",
                priority=2,
                target_area="missing.py",
                requires_file_edit=False,
                rationale="Check missing file",
            ),
        ],
        inspect_next=[],
        assumptions=[],
    )
    scan = {
        "files": [
            {"path": "backend/app/llm/loader.py"},
            {"path": "backend/app/llm/quantizer.py"},
            {"path": "backend/app/main.py"},
        ]
    }

    plan = build_execution_plan(decision, scan=scan)

    assert len(plan.steps) == 1
    assert plan.steps[0].target == "backend/app/llm"
    assert plan.steps[0].target_kind == "directory"
    assert "resolved from 'llm module'" in plan.steps[0].description
    assert plan.skipped_actions == [
        "Unresolved target for action_type=inspect_file: raw='missing.py', reason=no_match, candidates=[]"
    ]


def test_build_execution_plan_resolves_llm_file_to_llm_directory():
    decision = RepoDecision(
        repo_identity="ML app",
        architecture_summary="Contains model layer",
        confidence=0.8,
        risks=[],
        recommended_actions=[
            RecommendedAction(
                action_type="inspect_module",
                action="llm.py",
                priority=1,
                target_area="model training",
                requires_file_edit=False,
                rationale="Inspect model code",
            )
        ],
        inspect_next=[],
        assumptions=[],
    )
    scan = {
        "files": [
            {"path": "backend/app/llm/__init__.py"},
            {"path": "backend/app/llm/loader.py"},
            {"path": "data/vendor/requests/llm.py"},
        ]
    }

    plan = build_execution_plan(decision, scan=scan)

    assert len(plan.steps) == 1
    assert plan.steps[0].target == "backend/app/llm"
    assert plan.steps[0].target_kind == "directory"
    assert "resolved from 'llm.py'" in plan.steps[0].description


def test_build_execution_plan_chooses_highest_confidence_target():
    decision = RepoDecision(
        repo_identity="ML app",
        architecture_summary="Contains model layer",
        confidence=0.8,
        risks=[],
        recommended_actions=[
            RecommendedAction(
                action_type="inspect_module",
                action="llm.py",
                priority=1,
                target_area="model training",
                requires_file_edit=False,
                rationale="Inspect model code",
            )
        ],
        inspect_next=[],
        assumptions=[],
    )
    scan = {
        "files": [
            {"path": "backend/app/llm/__init__.py"},
            {"path": "backend/app/llm/loader.py"},
            {"path": "training/scripts/train_classifier.py"},
            {"path": "data/vendor/requests/llm.py"},
        ]
    }

    plan = build_execution_plan(decision, scan=scan)

    assert len(plan.steps) == 1
    assert plan.steps[0].target == "backend/app/llm"
    assert plan.steps[0].target_kind == "directory"
    assert "confidence=0.72" in plan.steps[0].description
