# repo_scanner/planner/planner.py
from __future__ import annotations

from ..llm_engine.output_schema import RepoDecision
from ..intelligence.priority_engine import PriorityEngine
from .action_router import route_action
from .plan_schema import ExecutionPlan
from .target_resolver import resolve_action_target


def build_execution_plan(
    decision: RepoDecision,
    scan: dict | None = None,
    graph_analysis: dict | None = None,
) -> ExecutionPlan:
    """
    Convert a validated RepoDecision into a safe proposal‑only ExecutionPlan.

    * If ``scan`` is provided, vague LLM targets are resolved to real repo paths.
    * If ``graph_analysis`` is provided, actions are re‑ranked using
      ``PriorityEngine``.  The continuous score is stored in the private
      ``_score`` attribute of each ``RecommendedAction``; the original
      ``priority`` field remains unchanged and is used only for display.
    """
    # ------------------------------------------------------------------
    # 1️⃣  Priority re‑ranking (new behaviour)
    # ------------------------------------------------------------------
    engine = PriorityEngine()
    for action in decision.recommended_actions:
        # Compute a continuous score; store it internally.
        score = engine.score(action, graph_analysis=graph_analysis or {})
        action._score = score  # private attribute for internal ordering

    # Sort actions by the internal score (high → high priority)
    sorted_actions = sorted(
        decision.recommended_actions, key=lambda a: a._score, reverse=True
    )

    # ------------------------------------------------------------------
    # 2️⃣  Build the plan (unchanged logic, just using the new order)
    # ------------------------------------------------------------------
    steps = []
    skipped = []

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
