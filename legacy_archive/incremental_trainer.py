# incremental_trainer.py
import joblib, pandas as pd
from train_classifier import pipeline   # the same pipeline definition

def incremental_update(new_examples: list[tuple[str, str]]) -> None:
    """
    new_examples – list of (code_snippet, label) tuples.
    Updates the saved model in‑place.
    """
    # Load existing model (or create a fresh one if missing)
    try:
        pipeline = joblib.load("code_pattern_clf.pkl")
    except FileNotFoundError:
        X, y = zip(*new_examples)
        pipeline.fit(list(X), list(y))
        joblib.dump(pipeline, "code_pattern_clf.pkl")
        return

    X_new, y_new = zip(*new_examples)

    # First call to partial_fit needs the full class list
    if not hasattr(pipeline.named_steps["clf"], "classes_"):
        classes = list(set(y_new))
    else:
        classes = pipeline.named_steps["clf"].classes_

    # Transform the new snippets with the existing TF‑IDF vectorizer
    X_new_vec = pipeline.named_steps["tfidf"].transform(X_new)

    pipeline.named_steps["clf"].partial_fit(X_new_vec, y_new, classes=classes)
    joblib.dump(pipeline, "code_pattern_clf.pkl")