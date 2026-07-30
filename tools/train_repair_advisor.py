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
DEFAULT_PHASE7_STRESS_ROWS = (
    ROOT / "benchmarks" / ".runs" / "phase7_stress_advisor_training_rows_latest.jsonl"
)


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


def load_phase7_stress_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Phase 7 stress rows not found: {path}")

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"Line {line_number} must be a JSON object.")
        rows.append(item)

    if not rows:
        raise ValueError(f"Phase 7 stress rows are empty: {path}")

    return rows


def build_phase7_stress_training_example(row: dict[str, Any]) -> RepairAdvisorTrainingExample:
    case_id = str(row.get("case_id", "")).strip()
    split = str(row.get("split", "")).strip()
    is_adversarial_stress = str(row.get("is_adversarial_stress", "")).strip()
    should_apply_patch = str(row.get("should_apply_patch", "")).strip()
    source_file = str(row.get("expected_source_file", ""))
    patch_risk = str(row.get("expected_patch_risk", "")).strip()
    bug_type = str(row.get("expected_bug_type", "")).strip()
    difficulty = str(row.get("expected_difficulty", "hard")).strip() or "hard"
    expected_failure_category = str(row.get("expected_failure_category", "")).strip()
    prompt = str(row.get("prompt", "")).strip()

    errors: list[str] = []
    if split != "phase7_stress":
        errors.append("split must be phase7_stress")
    if is_adversarial_stress != "true":
        errors.append("is_adversarial_stress must be true")
    if should_apply_patch != "false":
        errors.append("should_apply_patch must be false")
    if source_file != "":
        errors.append("expected_source_file must be empty")
    if patch_risk != "high":
        errors.append("expected_patch_risk must be high")
    if not bug_type:
        errors.append("expected_bug_type is required")
    if errors:
        label = case_id or "<unknown>"
        raise ValueError(f"Invalid Phase 7 stress row {label}: {', '.join(errors)}")

    goal_parts = [
        prompt or "Classify adversarial stress repair scenario.",
        f"case_id: {case_id}",
        f"expected_failure_category: {expected_failure_category}",
        "phase7_stress: true",
        "adversarial_stress: true",
        "patch_application_allowed: false",
    ]
    text = build_repair_feature_text(
        RepairAdvisorInput(
            goal="\n".join(part for part in goal_parts if part),
            failing_test_file=None,
            failing_test_name=None,
            assertion_summary=None,
            imported_modules=[],
            candidate_files=[],
            inspected_files=[],
            tool_actions=[],
        )
    )

    return RepairAdvisorTrainingExample(
        text=text,
        bug_type=bug_type,
        source_file="",
        difficulty=difficulty,
        patch_risk=patch_risk,
    )


def load_phase7_stress_examples(path: Path) -> list[RepairAdvisorTrainingExample]:
    examples: list[RepairAdvisorTrainingExample] = []
    for row in load_phase7_stress_rows(path):
        example = build_phase7_stress_training_example(row)
        weight_raw = str(row.get("training_weight", "1.0"))
        try:
            weight = max(1, round(float(weight_raw)))
        except ValueError:
            weight = 1
        examples.extend([example] * weight)
    return examples


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
    parser.add_argument(
        "--include-phase7-stress",
        action="store_true",
        help="Opt in to Phase 7 adversarial stress rows as a separate no-patch training split.",
    )
    parser.add_argument(
        "--phase7-stress-rows",
        default=str(DEFAULT_PHASE7_STRESS_ROWS),
        help="Path to Phase 7 adversarial stress rows JSONL.",
    )
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir).resolve()
    out_path = Path(args.out).resolve()

    examples = load_examples(cases_dir)
    normal_count = len(examples)
    phase7_stress_count = 0
    if args.include_phase7_stress:
        phase7_examples = load_phase7_stress_examples(Path(args.phase7_stress_rows).resolve())
        phase7_stress_count = len(phase7_examples)
        examples.extend(phase7_examples)

    model = train_model(examples)
    model["metadata"]["normal_examples"] = normal_count
    model["metadata"]["phase7_stress_examples"] = phase7_stress_count
    model["metadata"]["phase7_stress_rows"] = (
        str(Path(args.phase7_stress_rows).resolve()) if args.include_phase7_stress else ""
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_path)

    print(f"Trained repair advisor on {len(examples)} examples")
    print(f"Normal examples: {normal_count}")
    print(f"Phase 7 stress examples: {phase7_stress_count}")
    print(f"Saved model: {out_path}")


if __name__ == "__main__":
    main()
