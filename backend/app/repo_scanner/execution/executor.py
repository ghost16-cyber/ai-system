# repo_scanner/execution/executor.py
from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, List, Dict, Any

from ..workers.inspect_schema import InspectResult
from ..planner.plan_schema import ExecutionPlan


def execute_plan(
    plan: ExecutionPlan,
    repo_root: Path,
    inspect_func: Callable[[Path, str, str], InspectResult],
    max_steps: int = 3,
) -> List[Dict[str, Any]]:
    """
    Execute up to ``max_steps`` inspection steps of ``plan``.

    The executor now **trusts but verifies** the planner’s priority ordering:
      * Steps are sorted by their ``priority`` (1 = highest) before execution.
      * Only the first ``max_steps`` steps are run.

    Returns a list of plain dictionaries (``InspectResult.model_dump()``) so
    they can be fed directly into the feedback prompt.
    """
    # -----------------------------------------------------------------
    # 1️⃣  Ensure steps are ordered by priority (with exploration)
    # -----------------------------------------------------------------
    # 80% exploitation (best scores), 20% exploration (random noise)
    if random.random() < 0.2:
        # 🧠 EXPLORATION: Add random noise to encourage trying different targets
        plan.steps = sorted(
            plan.steps,
            key=lambda s: getattr(s, "_score", 0) + random.uniform(-1, 1),
            reverse=True
        )
    else:
        # EXPLOITATION: Use best scores deterministically
        plan.steps = sorted(
            plan.steps,
            key=lambda s: getattr(s, "_score", float(s.priority)),
            reverse=True
        )

    # -----------------------------------------------------------------
    # 2️⃣  Limit to the requested number of steps
    # -----------------------------------------------------------------
    limited_steps = plan.steps[:max_steps]

    results: List[Dict[str, Any]] = []
    for step in limited_steps:
        if step.step_type != "inspect":
            # Currently the executor only knows how to handle inspection steps.
            continue

        # Perform the inspection and store a plain dict for the feedback loop.
        result = inspect_func(repo_root, step.target, step.target_kind)
        results.append(result.model_dump())

    return results
