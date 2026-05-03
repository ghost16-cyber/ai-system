# Contextual Bandit - Learn from user feedback on suggestions
import numpy as np
from collections import defaultdict


class ContextualBandit:
    """
    Lightweight contextual bandit that learns which fixes to suggest.
    Uses UCB (Upper Confidence Bound) strategy.
    Runs entirely on CPU with minimal memory.
    """
    
    def __init__(self, num_actions, alpha=1.0):
        self.num_actions = num_actions
        self.alpha = alpha
        
        # Track counts and rewards per action
        self.action_counts = np.zeros(num_actions)
        self.action_rewards = np.zeros(num_actions)
    
    def select_action(self, state_features):
        """Select best action using UCB strategy."""
        # Compute UCB scores
        mean_rewards = self.action_rewards / (self.action_counts + 1)
        ucb_scores = mean_rewards + self.alpha * np.sqrt(
            np.log(self.action_counts.sum() + 1) / (self.action_counts + 1)
        )
        
        return np.argmax(ucb_scores)
    
    def update(self, action, reward):
        """Update bandit with user feedback."""
        self.action_counts[action] += 1
        self.action_rewards[action] += reward
    
    def get_stats(self):
        """Get statistics on actions."""
        mean_rewards = self.action_rewards / (self.action_counts + 1)
        return {
            "action_counts": self.action_counts.tolist(),
            "mean_rewards": mean_rewards.tolist(),
            "best_action": np.argmax(mean_rewards)
        }


class RewardTracker:
    """Track rewards from multiple sources (user feedback, lint scores, etc.)."""
    
    def __init__(self):
        self.rewards = []
    
    def log_feedback(self, suggestion_id, user_rating, lint_improvement=0, test_passed=False):
        """
        Log user feedback and automatic metrics.
        
        Args:
            suggestion_id: ID of the suggestion
            user_rating: -1 (bad), 0 (neutral), +1 (good)
            lint_improvement: lint score improvement (0-1)
            test_passed: whether generated test passed
        """
        reward = {
            "suggestion_id": suggestion_id,
            "user_rating": user_rating,
            "lint_improvement": lint_improvement,
            "test_passed": test_passed,
            "composite_reward": user_rating + 0.5 * lint_improvement + 0.3 * int(test_passed)
        }
        self.rewards.append(reward)
    
    def get_recent_rewards(self, n=100):
        """Get last n reward logs."""
        return self.rewards[-n:]
    
    def get_average_reward(self):
        """Get average composite reward."""
        if not self.rewards:
            return 0
        return np.mean([r["composite_reward"] for r in self.rewards])
