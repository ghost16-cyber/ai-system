# tools/evaluate_repair_advisor.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.advisors.repair_advisor import RepairAdvisor
from app.advisors.repair_labels import RepairAdvisorInput


DEFAULT_CASES_DIR = ROOT / "benchmarks" / "repair_cases"
DEFAULT_MODEL_PATH = ROOT / "models" / "repair_advisor" / "repair_advisor.joblib"


def load_metadata(case_dir: Path) -> Dict[str, Any]:
    """Read ``metadata.json`` for a case."""
    return json.loads((case_dir / "metadata.json").read_text(encoding="utf-8"))


def expected_source_file(
    metadata: Dict[str, Any], case_dir: Optional[Path] = None
) -> str:
    """
    Return the expected source file for a case.

    Mirrors the logic used during training.  If no file can be inferred,
    returns an empty string (instead of ``None``) so downstream comparisons
    stay safe.
    """
    for key in ("expected_source_file", "source_file", "target_file"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if case_dir is not None:
        src_dir = case_dir / "src"
        if src_dir.exists():
            source_files = sorted(
                p
                for p in src_dir.rglob("*.py")
                if p.name != "__init__.py"
            )
            if len(source_files) == 1:
                return source_files[0].relative_to(case_dir).as_posix()

        python_files = sorted(
            p
            for p in case_dir.rglob("*.py")
            if "__pycache__" not in p.parts
            and ".venv" not in p.parts
            and "venv" not in p.parts
            and "site-packages" not in p.parts
            and "tests" not in p.parts
            and p.name != "__init__.py"
        )
        if len(python_files) == 1:
            return python_files[0].relative_to(case_dir).as_posix()

    # Nothing could be inferred – callers treat an empty string as “unknown”.
    return ""


def build_input(metadata: Dict[str, Any], case_dir: Optional[Path] = None) -> RepairAdvisorInput:
    """Create the ``RepairAdvisorInput`` object expected by the model."""
    imports = metadata.get("imported_modules") or metadata.get("imports") or []
    candidate_files = metadata.get("candidate_files") or []

    source_file = expected_source_file(metadata, case_dir)
    test_file = metadata.get("expected_test_file")

    # If the benchmark did not provide an explicit candidate list, fall back
    # to the most likely files (source + test).
    if not candidate_files:
        candidate_files = [
            v for v in (source_file, test_file) if isinstance(v, str) and v.strip()
        ]

    return RepairAdvisorInput(
        goal=str(metadata.get("goal", "Fix the failing tests.")),
        failing_test_file=test_file,
        failing_test_name=None,
        assertion_summary=None,
        imported_modules=list(map(str, imports)),
        candidate_files=list(map(str, candidate_files)),
        inspected_files=[],
        tool_actions=[],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the shadow repair advisor.")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--out", default="", help="Path to write the full JSON report.")
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir).resolve()
    advisor = RepairAdvisor(args.model)

    rows: List[Dict[str, Any]] = []

    for case_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        metadata_path = case_dir / "metadata.json"
        if not metadata_path.is_file():
            continue

        metadata = load_metadata(case_dir)
        case_id = str(metadata.get("case_id", case_dir.name))
        expected_bug_type = str(metadata.get("bug_type", "unknown"))
        expected_source = expected_source_file(metadata, case_dir)

        # --------------------------------------------------------------
        # Predict – any exception from the model is caught so the loop can
        # continue processing the remaining cases.
        # --------------------------------------------------------------
        try:
            prediction = advisor.predict(build_input(metadata, case_dir))
            pred_bug_type = getattr(prediction, "bug_type", "")
            pred_source = getattr(prediction, "source_file", "")
            confidence = getattr(prediction, "confidence", None)
            reasons = getattr(prediction, "reasons", [])
        except Exception as exc:  # defensive programming
            pred_bug_type = ""
            pred_source = ""
            confidence = None
            reasons = [f"Prediction failed: {exc}"]
            prediction = None

        rows.append(
            {
                "case_id": case_id,
                "expected_bug_type": expected_bug_type,
                "predicted_bug_type": pred_bug_type,
                "bug_type_correct": pred_bug_type == expected_bug_type,
                "expected_source_file": expected_source,
                "predicted_source_file": pred_source,
                "source_file_correct": pred_source == expected_source,
                "confidence": confidence,
                "available": getattr(advisor, "available", None),
                "reasons": reasons,
            }
        )

    total = len(rows)
    bug_type_correct = sum(r["bug_type_correct"] for r in rows)
    source_file_correct = sum(r["source_file_correct"] for r in rows)

    summary = {
        "cases": total,
        "model_available": getattr(advisor, "available", None),
        "bug_type_accuracy": round(bug_type_correct / total, 4) if total else 0.0,
        "source_file_accuracy": round(source_file_correct / total, 4) if total else 0.0,
    }

    # --------------------------------------------------------------
    # Print a concise human‑readable summary.
    # --------------------------------------------------------------
    print(json.dumps(summary, indent=2, sort_keys=True))

    # --------------------------------------------------------------
    # Write the full report (summary + per‑case details) if requested.
    # --------------------------------------------------------------
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        full_report = {"summary": summary, "cases": rows}
        out_path.write_text(
            json.dumps(full_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Saved report: {out_path}")


if __name__ == "__main__":
    main()