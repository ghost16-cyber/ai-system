from __future__ import annotations

import argparse
import json
import sys
import time
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

from app.advisors.advisor_heads import ConstantHead, HEAD_FIELDS
from app.advisors.repair_features import build_repair_feature_text
from app.advisors.repair_labels import RepairAdvisorInput, RepairAdvisorTrainingExample


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


def _candidate_files(metadata: dict[str, Any], source_file: str) -> list[str]:
    candidates = metadata.get("candidate_files")
    if isinstance(candidates, list):
        values = [str(value) for value in candidates if isinstance(value, str) and value.strip()]
    else:
        values = []

    for value in (
        source_file,
        metadata.get("expected_test_file"),
        *(metadata.get("relevant_files") or []),
    ):
        if isinstance(value, str) and value.strip() and value not in values:
            values.append(value)
    return values


def build_training_example(metadata: dict[str, Any]) -> RepairAdvisorTrainingExample:
    goal = str(metadata.get("goal", "Fix the failing tests."))
    bug_type = str(metadata.get("bug_type", "unknown"))
    difficulty = str(metadata.get("difficulty", "easy"))
    patch_risk = str(metadata.get("patch_risk", "low"))
    source_file = infer_expected_source_file(metadata, metadata.get("_case_dir"))
    failing_test_file = str(metadata.get("expected_test_file", ""))
    imports = metadata.get("imported_modules") or metadata.get("imports") or []
    text = build_repair_feature_text(
        RepairAdvisorInput(
            goal=goal,
            failing_test_file=failing_test_file,
            failing_test_name=None,
            assertion_summary=str(metadata.get("expected_assertion", "")) or None,
            imported_modules=list(map(str, imports)),
            candidate_files=_candidate_files(metadata, source_file),
            inspected_files=[],
            tool_actions=[],
        )
    )

    return RepairAdvisorTrainingExample(
        text=text,
        bug_type=bug_type,
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


def train_head(texts: list[str], labels: list[str]) -> Any:
    unique = sorted(set(labels))
    if len(unique) == 1:
        return ConstantHead(unique[0])

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
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(texts, labels)
    return model


def train_model(examples: list[RepairAdvisorTrainingExample]) -> dict[str, Any]:
    if len(examples) < 2:
        raise ValueError("Need at least 2 examples to train repair advisor.")

    texts = [example.text for example in examples]
    label_map = {
        "bug_type": [example.bug_type for example in examples],
        "source_file": [example.source_file for example in examples],
        "difficulty": [example.difficulty for example in examples],
        "patch_risk": [example.patch_risk for example in examples],
    }
    heads = {head: train_head(texts, label_map[head]) for head in HEAD_FIELDS}
    return {
        "version": 2,
        "heads": heads,
        "metadata": {
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "num_examples": len(examples),
            "heads": list(HEAD_FIELDS),
            "cases_dir": str(DEFAULT_CASES_DIR),
        },
    }


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
