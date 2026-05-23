# repo_scanner/intelligence/priority_engine.py
from __future__ import annotations

from typing import Any, Dict

from ..llm_engine.output_schema import RecommendedAction


class PriorityEngine:
    """
    Priority engine that scores RecommendedAction objects.

    Combines:
      • deterministic base weight per action type
      • weak signal from LLM priority
      • graph signals (coupling, entry points, circular deps, complexity)
      • reward feedback from previous iterations
    """

    _BASE_SCORES = {
        "inspect_file": 10,
        "inspect_module": 9,
        "continue_analysis": 8,
        "fix_bug": 7,
        "add_tests": 6,
        "refactor": 5,
        "optimize": 5,
        "improve_docs": 4,
    }

    # ------------------------------------------------------------
    # 🔥 Reward-based adjustment (NEW - FIXED)
    # ------------------------------------------------------------
    def adjust_with_reward(
        self,
        action: RecommendedAction,
        history: list | None = None,
    ) -> float:
        """
        Average rewards for this target across all iterations.
        Prevents overfitting to a single high-reward result.
        Uses lower multiplier (0.3 vs 0.5) to balance exploration.
        """
        if not history:
            return 0.0

        rewards = [
            r.get("reward", 0.0)
            for r in history
            if action.target_area == r.get("target")
        ]

        if not rewards:
            return 0.0

        # Average across all iterations to prevent overfitting
        avg_reward = sum(rewards) / len(rewards)
        
        # Lower multiplier (0.3 instead of 0.5) to prevent domination
        return avg_reward * 0.3

    # ------------------------------------------------------------
    # 🔥 Main scoring function
    # ------------------------------------------------------------
    def score(
        self,
        action: RecommendedAction,
        graph_analysis: Dict[str, Any] | None = None,
    ) -> float:

        # -------------------------
        # 1. Base action importance
        # -------------------------
        base = self._BASE_SCORES.get(action.action_type, 1)

        # -------------------------
        # 2. LLM priority (weak signal)
        # -------------------------
        priority_bonus = (6 - action.priority) * 0.5

        # -------------------------
        # 3. Graph signals
        # -------------------------
        coupling_bonus = 0.0
        entry_bonus = 0.0
        circular_bonus = 0.0
        complexity_bonus = 0.0
        reward_bonus = 0.0

        if graph_analysis:
            # FIXED: correct variable name
            history = graph_analysis.get("history", [])
            reward_bonus = self.adjust_with_reward(action, history)

            # High coupling
            high_coupling = graph_analysis.get("high_coupling_files", {})
            if isinstance(high_coupling, dict) and action.target_area in high_coupling:
                coupling_bonus = 2.0

            # Circular dependencies
            circular_deps = graph_analysis.get("circular_dependencies", [])
            if isinstance(circular_deps, list) and action.target_area in circular_deps:
                circular_bonus = 2.0

            # Entry points
            entry_points = graph_analysis.get("entry_points", [])
            if isinstance(entry_points, list) and any(
                ep in action.target_area for ep in entry_points
            ):
                entry_bonus = 1.5

            # Complexity (normalized)
            complexity_map = graph_analysis.get("complexity", {})
            if isinstance(complexity_map, dict):
                comp = complexity_map.get(action.target_area)
                if isinstance(comp, (int, float)):
                    complexity_bonus = min(comp / 10, 1.5)

        # -------------------------
        # 4. Final weighted score
        # -------------------------
        total_score = base + (
            0.5 * priority_bonus
            + 1.0 * coupling_bonus
            + 1.2 * circular_bonus
            + 0.8 * entry_bonus
            + 0.5 * complexity_bonus
            + 1.0 * reward_bonus   # 🔥 NEW: reward integrated properly
        )

        return total_score
