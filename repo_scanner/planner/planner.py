# repo_scanner/planner/planner.py

from __future__ import annotations

from repo_scanner.llm_engine.output_schema import RepoDecision
from repo_scanner.planner.action_router import route_action
from repo_scanner.planner.plan_schema import ExecutionPlan
from repo_scanner.planner.target_resolver import resolve_action_target


def build_execution_plan(
    decision: RepoDecision,
    scan: dict | None = None,
) -> ExecutionPlan:
    """
    Convert a validated RepoDecision into a safe proposal-only ExecutionPlan.

    If scan is provided, vague LLM targets are resolved against real repo files/folders.
    """

    steps = []
    skipped = []

    sorted_actions = sorted(
        decision.recommended_actions,
        key=lambda action: action.priority,
    )

    for action in sorted_actions:
        resolved = None

        if scan is not None:
            resolved = resolve_action_target(action, scan)

            if resolved.resolved_target is None:
                skipped.append(
                    f"Unresolved target for action_type={action.action_type}: "
                    f"raw={resolved.raw_target!r}, reason={resolved.reason}, "
                    f"candidates={resolved.candidates}"
                )
                continue

            action.target_area = resolved.resolved_target

        step = route_action(action)

        if step is None:
            skipped.append(
                f"Unsupported action_type={action.action_type} target={action.target_area}"
            )
            continue

        if resolved is not None:
            step.target_kind = resolved.target_kind
            step.description = (
                f"{step.description} "
                f"(resolved from {resolved.raw_target!r}, confidence={resolved.confidence:.2f})"
            )

        steps.append(step)

    return ExecutionPlan(
        summary=f"Proposal-only plan for: {decision.repo_identity}",
        steps=steps,
        skipped_actions=skipped,
    )
