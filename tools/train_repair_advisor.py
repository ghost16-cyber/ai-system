from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.advisors.repair_labels import RepairAdvisorTrainingExample


DEFAULT_CASES_DIR = ROOT / "benchmarks" / "repair_cases"
DEFAULT_MODEL_PATH = ROOT / "models" / "repair_advisor" / "repair_advisor.joblib"


def load_metadata(case_dir: Path) -> dict[str, Any]:
    metadata_path = case_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing metadata.json in {case_dir}")

    return json.loads(metadata_path.read_text(encoding="utf-8"))


def infer_expected_source_file(metadata: dict[str, Any], case_dir: Path | None = None) -> str:
    for key in ("expected_source_file", "source_file", "target_file"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if case_dir is not None:
        src_dir = case_dir / "src"
        if src_dir.exists():
            source_files = sorted(
                path
                for path in src_dir.rglob("*.py")
                if path.name != "__init__.py"
            )
            if len(source_files) == 1:
                return source_files[0].relative_to(case_dir).as_posix()

        python_files = sorted(
            path
            for path in case_dir.rglob("*.py")
            if "__pycache__" not in path.parts
            and ".venv" not in path.parts
            and "venv" not in path.parts
            and "site-packages" not in path.parts
            and "tests" not in path.parts
            and path.name != "__init__.py"
        )
        if len(python_files) == 1:
            return python_files[0].relative_to(case_dir).as_posix()

    case_id = str(metadata.get("case_id", "unknown"))
    raise ValueError(
        f"Case {case_id} has no expected_source_file/source_file/target_file "
        "and source file could not be inferred from the case folder."
    )


def build_training_example(metadata: dict[str, Any]) -> RepairAdvisorTrainingExample:
    goal = str(metadata.get("goal", "Fix the failing tests."))
    case_id = str(metadata.get("case_id", "unknown"))
    bug_type = str(metadata.get("bug_type", "unknown"))
    difficulty = str(metadata.get("difficulty", "easy"))
    patch_risk = str(metadata.get("patch_risk", "low"))
    source_file = infer_expected_source_file(metadata, metadata.get("_case_dir"))
    failing_test_file = str(metadata.get("expected_test_file", ""))
    imports = metadata.get("imported_modules") or metadata.get("imports") or []

    text = "\n".join(
        [
            f"case_id: {case_id}",
            f"goal: {goal}",
            f"bug_type_hint: {bug_type}",
            f"failing_test_file: {failing_test_file}",
            "imports: " + " ".join(map(str, imports)),
            f"expected_source_file_hint: {source_file}",
        ]
    )

    label = json.dumps(
        {
            "bug_type": bug_type,
            "source_file": source_file,
            "difficulty": difficulty,
            "patch_risk": patch_risk,
        },
        sort_keys=True,
    )

    return RepairAdvisorTrainingExample(
        text=text,
        bug_type=label,
        source_file=source_file,
        difficulty=difficulty,
        patch_risk=patch_risk,
    )


def load_examples(cases_dir: Path) -> list[RepairAdvisorTrainingExample]:
    examples: list[RepairAdvisorTrainingExample] = []

    for case_dir in sorted(path for path in cases_dir.iterdir() if path.is_dir()):
        metadata_path = case_dir / "metadata.json"
        if not metadata_path.exists():
            continue

        metadata = load_metadata(case_dir)
        metadata["_case_dir"] = case_dir
        examples.append(build_training_example(metadata))

    return examples


def train_model(examples: list[RepairAdvisorTrainingExample]) -> Pipeline:
    if len(examples) < 2:
        raise ValueError("Need at least 2 examples to train repair advisor.")

    texts = [example.text for example in examples]
    labels = [example.bug_type for example in examples]

    model = Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                ),
            ),
        ]
    )

    model.fit(texts, labels)
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train shadow repair advisor.")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--out", default=str(DEFAULT_MODEL_PATH))
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir).resolve()
    out_path = Path(args.out).resolve()

    examples = load_examples(cases_dir)
    model = train_model(examples)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_path)

    print(f"Trained repair advisor on {len(examples)} examples")
    print(f"Saved model: {out_path}")


if __name__ == "__main__":
    main()

