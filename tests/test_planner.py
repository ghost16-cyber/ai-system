import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_scanner.llm_engine.output_schema import RecommendedAction, RepoDecision
from repo_scanner.planner import build_execution_plan


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
                target_area="repo_scanner/scanner.py",
                requires_file_edit=True,
                rationale="Reduce complexity",
            ),
            RecommendedAction(
                action_type="inspect_module",
                action="Inspect rules",
                priority=1,
                target_area="repo_scanner/analysis_engine/rules.py",
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
