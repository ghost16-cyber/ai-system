# Fast Pattern Classifier - CPU-based pattern detection
import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.pipeline import Pipeline
import pandas as pd


class PatternClassifier:
    """
    Fast CPU-based pattern classifier using TF-IDF + SGDClassifier.
    Runs entirely on CPU (0 VRAM).
    """
    """Detect common code patterns and anti-patterns for quick wins.
    Trained on a small dataset of labeled code snippets.
    """
    __version__ = "0.1.0"

    def __init__(self, model_path=None):
        self.pipeline = None
        if model_path:
            self.load(model_path)
    
    def train(self, code_snippets, labels, model_path=None):
        print(f"Training PatternClassifier on {len(code_snippets)} samples...")

        # NOTE: use logistic loss → probability output
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(
                analyzer="char",
                ngram_range=(3, 5),
                min_df=1,
                max_features=5000,
                sublinear_tf=True,
            )),
            ("clf", SGDClassifier(
                loss="log_loss",          # <-- changed from "hinge"
                penalty="l2",
                max_iter=1000,
                learning_rate="optimal",
                class_weight="balanced",
            )),
        ])

        self.pipeline.fit(code_snippets, labels)
        print(f"✓ Training complete. Classes: {list(self.pipeline.classes_)}")
        if model_path:
            self.save(model_path)
            print(f"✓ Model saved to {model_path}")
    
    def predict(self, code_snippet):
        """Predict pattern label for a single code snippet."""
        if self.pipeline is None:
            raise ValueError("Model not trained or loaded")
        return self.pipeline.predict([code_snippet])[0]
    
    def predict_proba(self, code_snippet):
        """Get prediction probabilities."""
        if self.pipeline is None:
            raise ValueError("Model not trained or loaded")
        try:
            # Transform the input
            tfidf = self.pipeline.named_steps['tfidf']
            transformed = tfidf.transform([code_snippet])
            
            # Get probabilities from classifier
            clf = self.pipeline.named_steps['clf']
            probabilities = clf.predict_proba(transformed)[0]
            return probabilities
        except Exception as e:
            print(f"Warning: Could not get probabilities: {e}")
            # Return uniform probabilities if prediction fails
            if hasattr(self.pipeline.named_steps['clf'], 'classes_'):
                n_classes = len(self.pipeline.named_steps['clf'].classes_)
                return [1.0/n_classes] * n_classes
            return [1.0]  # Default fallback
    
    def save(self, path):
        """Save trained model to disk."""
        joblib.dump(self.pipeline, path)
        print(f"✓ Model saved to {path}")
    
    def load(self, path):
        """Load trained model from disk."""
        self.pipeline = joblib.load(path)
        print(f"✓ Model loaded from {path}")


if __name__ == "__main__":
    # Example usage
    clf = PatternClassifier()
    
    # Train on sample data
    samples = [
        "for i in range(len(items)): print(items[i])",
        "for item in items: print(item)",
        "x = x + 1",
        "x += 1"
    ]
    labels = ["inefficient_loop", "good_loop", "non_pythonic", "pythonic"]
    
    clf.train(samples, labels, "model.pkl")
    
    # Predict
    test = "for i in range(len(data)): process(data[i])"
    print(f"Prediction: {clf.predict(test)}")
