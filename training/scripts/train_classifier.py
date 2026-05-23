# Train Pattern Classifier - Use this to train the fast ML model
import pandas as pd
import sys
from pathlib import Path

# Add the project package to the import path.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.app.ml.classifier import PatternClassifier


def main():
    print("="*60)
    print("PATTERN CLASSIFIER TRAINING")
    print("="*60)
    
    # Load data
    csv_path = "data/processed/code_patterns.csv"
    if not Path(csv_path).exists():
        print(f"Error: {csv_path} not found")
        return
    
    df = pd.read_csv(csv_path)
    X = df["code_snippet"].astype(str)
    y = df["label"]
    
    print(f"Dataset: {len(X)} samples, {len(y.unique())} classes")
    print(f"Class distribution:\n{y.value_counts()}\n")
    
    # Train classifier
    clf = PatternClassifier()
    clf.train(X, y, "data/models/pattern_clf.pkl")
    
    print("\n✓ Training complete!")
    print(f"Model saved to: data/models/pattern_clf.pkl")


if __name__ == "__main__":
    main()
