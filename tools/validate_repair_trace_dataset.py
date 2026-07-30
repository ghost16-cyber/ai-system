from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_INPUT = "benchmarks/.runs/repair_trace_dataset_latest.jsonl"
DEFAULT_OUTPUT = "benchmarks/.runs/repair_trace_dataset_validation_latest.json"

VALID_ACTIONS = {
    "search_files",
    "read_file",
    "analyze_ast",
    "run_tests",
    "validate_syntax",
    "propose_patch",
    "apply_patch",
    "rollback_patch",
    "final_response",
}

VALID_IDEAL_ACTIONS = (VALID_ACTIONS - {"apply_patch"}) | {
    "inspect_imports",
    "switch_target_file",
    "stop_repeated_reads",
}

VALID_FAILURE_MODES = {
    "none",
    "missing_dependency",
    "stale_read_loop",
    "policy_block",
    "no_patch_proposed",
    "tests_failed_after_patch",
    "max_steps_reached",
    "unknown_failure",
}

REQUIRED_FIELDS = {
    "case_id",
    "step_index",
    "timestamp",
    "advisor_runtime_mode",
    "partial_actions",
    "files_read",
    "test_status",
    "error_category",
    "advisor_next_action",
    "actual_next_action",
    "ideal_next_action",
    "final_status",
    "failure_mode",
    "intervention_needed",
    "split",
    "source_trace",
    "label_source",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate repair trace dataset rows.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = load_jsonl(Path(args.input).resolve())
    report = validate_rows(rows)
    report["input"] = str(Path(args.input).resolve())
    report["phase"] = "phase9_repair_trace_dataset_validation"

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nRepair trace dataset validation written to: {output}")
    return 0 if report["passed"] else 1


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"Line {line_number} must be a JSON object.")
        rows.append(item)
    return rows


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    split_counts: Counter[str] = Counter()
    ideal_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    rows_by_trace: dict[str, list[dict[str, Any]]] = {}

    if not rows:
        errors.append("Dataset must contain at least one row.")

    for index, row in enumerate(rows):
        row_id = f"{row.get('source_trace', '<unknown>')}:{row.get('step_index', index)}"
        missing = sorted(REQUIRED_FIELDS - set(row.keys()))
        if missing:
            errors.append(f"{row_id}: missing fields {missing}")
            continue

        case_id = str(row["case_id"])
        split = str(row["split"])
        actual = str(row["actual_next_action"])
        ideal = str(row["ideal_next_action"])
        failure_mode = str(row["failure_mode"])
        partial_actions = row["partial_actions"]
        step_index = row["step_index"]

        if split not in {"train", "test"}:
            errors.append(f"{row_id}: split must be train or test.")
        elif split != stable_split(case_id):
            errors.append(f"{row_id}: split is not stable for case_id.")

        if actual not in VALID_ACTIONS:
            errors.append(f"{row_id}: invalid actual_next_action={actual!r}")
        if ideal not in VALID_IDEAL_ACTIONS:
            errors.append(f"{row_id}: invalid ideal_next_action={ideal!r}")
        if failure_mode not in VALID_FAILURE_MODES:
            errors.append(f"{row_id}: invalid failure_mode={failure_mode!r}")
        if not isinstance(partial_actions, list):
            errors.append(f"{row_id}: partial_actions must be a list.")
        elif any(str(action) not in VALID_ACTIONS for action in partial_actions):
            errors.append(f"{row_id}: partial_actions contains invalid action.")
        if not isinstance(step_index, int) or step_index < 1:
            errors.append(f"{row_id}: step_index must be a positive integer.")
        elif isinstance(partial_actions, list) and len(partial_actions) != step_index - 1:
            errors.append(f"{row_id}: partial_actions leaks future or skips history.")
        if row["advisor_next_action"] and str(row["advisor_next_action"]) not in (VALID_ACTIONS | VALID_IDEAL_ACTIONS):
            errors.append(f"{row_id}: invalid advisor_next_action.")
        if not isinstance(row["intervention_needed"], bool):
            errors.append(f"{row_id}: intervention_needed must be boolean.")
        if not str(row["label_source"]):
            errors.append(f"{row_id}: label_source is required.")

        split_counts[split] += 1
        ideal_counts[ideal] += 1
        failure_counts[failure_mode] += 1
        rows_by_trace.setdefault(str(row["source_trace"]), []).append(row)

    for trace_name, trace_rows in rows_by_trace.items():
        indexes = [int(row["step_index"]) for row in trace_rows]
        if indexes != sorted(indexes):
            errors.append(f"{trace_name}: rows must be ordered by step_index.")
        if indexes and indexes != list(range(1, max(indexes) + 1)):
            errors.append(f"{trace_name}: step_index values must be contiguous.")

    return {
        "passed": not errors,
        "row_count": len(rows),
        "trace_count": len(rows_by_trace),
        "split_counts": dict(sorted(split_counts.items())),
        "ideal_next_action_distribution": dict(sorted(ideal_counts.items())),
        "failure_mode_distribution": dict(sorted(failure_counts.items())),
        "errors": errors,
    }


def stable_split(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 5
    return "test" if bucket == 0 else "train"


if __name__ == "__main__":
    raise SystemExit(main())
