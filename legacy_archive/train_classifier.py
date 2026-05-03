# train_classifier.py
import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from sklearn.metrics import classification_report, accuracy_score
from sklearn.linear_model import SGDClassifier

# 1 - Load data
df = pd.read_csv("code_patterns.csv")
X = df["code_snippet"].astype(str)
y = df["label"]

print(f"Dataset: {len(X)} samples, {len(y.unique())} classes")
print(f"Class distribution:\n{y.value_counts()}\n")

# 2 - Train / test split (20% hold-out)
# Note: For small datasets, we remove stratify to avoid class imbalance issues
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=None
)

print(f"Training set: {len(X_train)} samples")
print(f"Test set: {len(X_test)} samples\n")

# 3 - Build pipeline
pipeline = Pipeline([
    ("tfidf", TfidfVectorizer(
        analyzer="char", ngram_range=(3,5),
        min_df=1, max_features=5000, sublinear_tf=True)),
    ("clf", SGDClassifier(
        loss="hinge",          # SVM‑style
        penalty="l2",
        max_iter=1000,
        learning_rate="optimal",
        class_weight="balanced"))
])

# 4 - Train
print("Training model...")
pipeline.fit(X_train, y_train)

# 5 - Evaluate
y_pred = pipeline.predict(X_test)
print("\n=== Model Evaluation ===")
print("Accuracy:", accuracy_score(y_test, y_pred))
print("\nClassification Report:")
print(classification_report(y_test, y_pred))

# 6 - Persist model & vectorizer together
joblib.dump(pipeline, "code_pattern_clf.pkl")
print("\nModel saved to code_pattern_clf.pkl")