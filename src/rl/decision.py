# src/rl/decision.py
"""
Strategy decision using a contextual bandit.

The bandit learns from a scalar confidence feature and a scalar reward.
"""

import numpy as np
from .bandit import ContextualBandit


class StrategyDecider:
    """
    Choose between a cheap classifier‑only path (action 0) and the full
    RAG+LLM path (action 1).  The bandit updates its policy based on
    user‑provided reward signals (e.g. explicit feedback or downstream
    metrics).

    Parameters
    ----------
    num_actions : int, default 2
        Number of possible strategies.
    alpha : float, default 1.0
        Exploration coefficient for the UCB algorithm.
    """

    def __init__(self, num_actions: int = 2, alpha: float = 1.0):
        self.bandit = ContextualBandit(num_actions=num_actions, alpha=alpha)

    def choose(self, confidence: float) -> int:
        """
        Select an action based on the current confidence.

        ``confidence`` is a scalar in ``[0, 1]``; we use it directly as the
        feature vector for the bandit.
        """
        features = np.array([confidence])
        return int(self.bandit.select_action(features))

    def update(self, action: int, reward: float) -> None:
        """
        Feed back a reward for the chosen ``action``.
        ``reward`` should be a non‑negative scalar (higher = better).
        """
        self.bandit.update(action, reward)

    def get_stats(self) -> dict:
        """Expose internal bandit statistics for debugging."""
        return self.bandit.get_stats()