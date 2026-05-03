# repo_scanner/planner/planner.py

from __future__ import annotations

from repo_scanner.llm_engine.output_schema import RepoDecision
from repo_scanner.planner.action_router import route_action
from repo_scanner.planner.plan_schema import ExecutionPlan


def build_execution_plan(decision: RepoDecision) -> ExecutionPlan:
    """
    Convert a validated RepoDecision into a safe proposal-only ExecutionPlan.
    """

    steps = []
    skipped = []

    sorted_actions = sorted(
        decision.recommended_actions,
        key=lambda action: action.priority,
    )

    for action in sorted_actions:
        step = route_action(action)

        if step is None:
            skipped.append(
                f"Unsupported action_type={action.action_type} target={action.target_area}"
            )
            continue

        steps.append(step)

    return ExecutionPlan(
        summary=f"Proposal-only plan for: {decision.repo_identity}",
        steps=steps,
        skipped_actions=skipped,
    )