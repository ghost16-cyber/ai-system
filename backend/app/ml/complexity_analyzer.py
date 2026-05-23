# Code Complexity Analyzer - LightGBM-based complexity scoring
from sklearn.ensemble import GradientBoostingClassifier
import joblib


class ComplexityAnalyzer:
    """
    Analyze code complexity and generate improvement scores.
    Uses gradient boosting for fast predictions on CPU.
    """
    
    def __init__(self, model_path=None):
        self.model = None
        if model_path:
            self.load(model_path)
    
    def _extract_features(self, code_snippet):
        """Extract numerical features from code."""
        lines = code_snippet.split('\n')
        features = {
            'line_count': len(lines),
            'avg_line_length': sum(len(l) for l in lines) / len(lines) if lines else 0,
            'indent_levels': max(
                (len(l) - len(l.lstrip())) // 4 + 1 
                for l in lines if l.strip()
            ) if any(l.strip() for l in lines) else 1,
            'has_loop': 1 if any('for ' in l or 'while ' in l for l in lines) else 0,
            'has_function': 1 if any('def ' in l for l in lines) else 0,
        }
        return features
    
    def train(self, code_snippets, complexity_scores, model_path=None):
        """Train complexity analyzer."""
        print(f"Training ComplexityAnalyzer on {len(code_snippets)} samples...")
        
        X = [self._extract_features(code) for code in code_snippets]
        y = complexity_scores
        
        self.model = GradientBoostingClassifier(
            n_estimators=50,
            max_depth=5,
            learning_rate=0.1
        )
        self.model.fit(X, y)
        print("✓ ComplexityAnalyzer trained")
        
        if model_path:
            self.save(model_path)
    
    def analyze(self, code_snippet):
        """Analyze code and return complexity score (0-100)."""
        features = self._extract_features(code_snippet)
        score = self.model.predict([list(features.values())])[0]
        return max(0, min(100, score))  # Clamp to 0-100
    
    def save(self, path):
        """Save model to disk."""
        joblib.dump(self.model, path)
        print(f"✓ Model saved to {path}")
    
    def load(self, path):
        """Load model from disk."""
        self.model = joblib.load(path)
        print(f"✓ Model loaded from {path}")
