# repo_scanner/planner/action_router.py

from __future__ import annotations

from repo_scanner.llm_engine.output_schema import RecommendedAction
from repo_scanner.planner.plan_schema import PlanStep


def route_action(action: RecommendedAction) -> PlanStep | None:
    """
    Convert a validated LLM recommendation into a safe planner step.

    Planner v1 is proposal-only:
    - no file edits
    - no command execution
    - no autonomous changes
    """

    action_type = action.action_type
    target = action.target_area or action.action

    if action_type in {"inspect_file", "inspect_module"}:
        return PlanStep(
            step_type="inspect",
            source_action_type=action_type,
            target=target,
            description=f"Inspect {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=False,
            rationale=action.rationale,
        )

    if action_type == "continue_analysis":
        return PlanStep(
            step_type="analyze",
            source_action_type=action_type,
            target=target,
            description=f"Continue static analysis on {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=False,
            rationale=action.rationale,
        )

    if action_type == "add_tests":
        return PlanStep(
            step_type="propose_tests",
            source_action_type=action_type,
            target=target,
            description=f"Propose tests for {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=True,
            rationale=action.rationale,
        )

    if action_type == "refactor":
        return PlanStep(
            step_type="propose_refactor",
            source_action_type=action_type,
            target=target,
            description=f"Propose refactor for {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=True,
            rationale=action.rationale,
        )

    if action_type == "fix_bug":
        return PlanStep(
            step_type="propose_fix",
            source_action_type=action_type,
            target=target,
            description=f"Propose bug fix for {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=True,
            rationale=action.rationale,
        )

    if action_type == "optimize":
        return PlanStep(
            step_type="propose_optimization",
            source_action_type=action_type,
            target=target,
            description=f"Propose optimization for {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=True,
            rationale=action.rationale,
        )

    if action_type == "improve_docs":
        return PlanStep(
            step_type="propose_docs",
            source_action_type=action_type,
            target=target,
            description=f"Propose documentation improvement for {target}",
            priority=action.priority,
            allowed_to_modify=False,
            requires_approval=True,
            rationale=action.rationale,
        )

    return None