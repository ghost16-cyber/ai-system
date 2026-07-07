from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_INPUT = "benchmarks/.runs/repair_trace_dataset_latest.jsonl"
DEFAULT_OUTPUT = "benchmarks/.runs/repair_trace_dataset_analysis_latest.json"
START_ACTION = "<START>"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze repair trace dataset rows.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--top-patterns", type=int, default=10)
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    rows = load_jsonl(input_path)
    report = analyze_rows(rows, input_path=input_path, top_patterns=args.top_patterns)

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nRepair trace dataset analysis written to: {output}")
    return 0


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
            raise ValueError(f"{path}:{line_number} must be a JSON object.")
        rows.append(item)
    return rows


def analyze_rows(
    rows: list[dict[str, Any]],
    *,
    input_path: Path | None = None,
    top_patterns: int = 10,
) -> dict[str, Any]:
    split_counts = Counter(str(row.get("split", "")) for row in rows)
    ideal_counts = Counter(str(row.get("ideal_next_action", "")) for row in rows)
    failure_counts = Counter(
        str(row.get("failure_mode", ""))
        for row in rows
        if "failure_mode" in row
    )
    intervention_counts = Counter(
        str(row.get("intervention_needed"))
        for row in rows
        if "intervention_needed" in row
    )

    partial_lengths: list[int] = []
    last_actions: Counter[str] = Counter()
    partial_patterns: Counter[tuple[str, ...]] = Counter()

    for row in rows:
        actions = normalized_actions(row.get("partial_actions"))
        partial_lengths.append(len(actions))
        last_actions[actions[-1] if actions else START_ACTION] += 1
        if actions:
            partial_patterns[tuple(actions)] += 1

    largest_class = None
    largest_class_count = 0
    if ideal_counts:
        largest_class, largest_class_count = ideal_counts.most_common(1)[0]
    largest_class_fraction = (
        largest_class_count / len(rows)
        if rows
        else 0.0
    )

    return {
        "phase": "phase10_repair_trace_dataset_analysis",
        "input": str(input_path) if input_path else "",
        "total_rows": len(rows),
        "train_rows": split_counts.get("train", 0),
        "test_rows": split_counts.get("test", 0),
        "split_counts": dict(sorted(split_counts.items())),
        "ideal_next_action_distribution": dict(sorted(ideal_counts.items())),
        "failure_mode_distribution": dict(sorted(failure_counts.items())),
        "intervention_needed_distribution": dict(sorted(intervention_counts.items())),
        "partial_trace_length": summarize_lengths(partial_lengths),
        "last_action_distribution": dict(sorted(last_actions.items())),
        "contains_apply_patch_ideal_next_action": ideal_counts.get("apply_patch", 0) > 0,
        "apply_patch_ideal_next_action_count": ideal_counts.get("apply_patch", 0),
        "top_repeated_partial_action_patterns": format_patterns(partial_patterns, top_patterns),
        "largest_class": largest_class,
        "largest_class_fraction": round(largest_class_fraction, 6),
        "class_imbalance_warning": largest_class_fraction > 0.60,
    }


def normalized_actions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(action) for action in value]


def summarize_lengths(lengths: list[int]) -> dict[str, float | int]:
    if not lengths:
        return {"min": 0, "mean": 0.0, "max": 0}
    return {
        "min": min(lengths),
        "mean": round(mean(lengths), 6),
        "max": max(lengths),
    }


def format_patterns(
    patterns: Counter[tuple[str, ...]],
    limit: int,
) -> list[dict[str, Any]]:
    repeated = [
        (pattern, count)
        for pattern, count in patterns.items()
        if count > 1
    ]
    repeated.sort(key=lambda item: (-item[1], item[0]))
    return [
        {"pattern": list(pattern), "count": count}
        for pattern, count in repeated[: max(limit, 0)]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
