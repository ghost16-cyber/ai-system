# Reward tracking for RL feedback loop
import json
from pathlib import Path
from datetime import datetime


class RewardTracker:
    """Track and log rewards from multiple sources."""
    
    def __init__(self, log_path="data/logs/rewards.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
    
    def log_feedback(self, suggestion_id, user_rating, lint_improvement=0, test_passed=False):
        """Log user feedback and automatic metrics."""
        reward = {
            "timestamp": datetime.now().isoformat(),
            "suggestion_id": suggestion_id,
            "user_rating": user_rating,
            "lint_improvement": lint_improvement,
            "test_passed": test_passed,
            "composite_reward": user_rating + 0.5 * lint_improvement + 0.3 * int(test_passed)
        }
        
        with open(self.log_path, 'a') as f:
            f.write(json.dumps(reward) + '\n')
    
    def get_average_reward(self, last_n=100):
        """Get average reward from last N records."""
        records = []
        with open(self.log_path, 'r') as f:
            for line in f:
                records.append(json.loads(line))
        
        if not records:
            return 0
        
        recent = records[-last_n:]
        return sum(r["composite_reward"] for r in recent) / len(recent)
