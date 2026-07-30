from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from scripts.audit_intent_dataset import (
    REQUIRED_COLUMNS,
    VALID_LABELS,
    VALID_STATUSES,
    normalize_message,
    validate_dataset,
)


DEFAULT_INPUTS = (
    Path("data/specialists/intent_examples.csv"),
    Path("data/specialists/intent_examples_hf_stackoverflow_seed.csv"),
    Path("data/specialists/intent_examples_stacklite_seed.csv"),
)
DEFAULT_OUTPUT = Path("data/specialists/intent_examples_combined.csv")
SOURCE_PRIORITY = {
    "astra": 0,
    "manual": 0,
    "chat_run": 0,
    "imported": 0,
    "huggingface_stackoverflow": 1,
    "stackoverflow": 2,
    "stacklite": 2,
}


def merge_datasets(
    input_paths: tuple[str | Path, ...] = DEFAULT_INPUTS,
    output_path: str | Path = DEFAULT_OUTPUT,
    *,
    max_per_label: int = 150,
) -> pd.DataFrame:
    frames = load_available_datasets(input_paths)
    if not frames:
        combined = pd.DataFrame(columns=REQUIRED_COLUMNS)
    else:
        combined = pd.concat(frames, ignore_index=True)
        combined = _dedupe_by_priority(combined)
        combined = _cap_per_label(combined, max_per_label=max_per_label)
        combined = combined.loc[:, list(REQUIRED_COLUMNS)]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output, index=False)
    return combined


def load_available_datasets(input_paths: tuple[str | Path, ...]) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for index, path_value in enumerate(input_paths):
        path = Path(path_value)
        if not path.exists():
            continue
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        errors = validate_dataset(frame)
        if errors:
            raise ValueError(f"{path}: {'; '.join(errors)}")
        frame = frame.loc[
            frame["final_label"].isin(VALID_LABELS)
            & frame["label_status"].isin(VALID_STATUSES)
        ].copy()
        frame["_input_rank"] = index
        frame["_source_rank"] = frame["source"].map(source_priority).astype(int)
        frame["_normalized_message"] = frame["user_message"].map(normalize_message)
        frames.append(frame)
    return frames


def source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get(str(source), 1)


def _dedupe_by_priority(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values(
        by=["_source_rank", "_input_rank"],
        ascending=[True, True],
        kind="mergesort",
    )
    return ordered.drop_duplicates(subset=["_normalized_message"], keep="first")


def _cap_per_label(frame: pd.DataFrame, *, max_per_label: int) -> pd.DataFrame:
    selected = []
    counts: Counter[str] = Counter()
    for _, row in frame.iterrows():
        label = str(row["final_label"])
        if counts[label] >= max_per_label:
            continue
        counts[label] += 1
        selected.append(row)
    if not selected:
        return pd.DataFrame(columns=frame.columns)
    return pd.DataFrame(selected)


def print_distribution(frame: pd.DataFrame, output_path: str | Path) -> None:
    print(f"Wrote {len(frame)} examples to {output_path}")
    print("Final distribution:")
    distribution = frame["final_label"].value_counts().sort_index().to_dict() if not frame.empty else {}
    for label, count in distribution.items():
        print(f"- {label}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge Astra intent dataset CSVs.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-per-label", type=int, default=150)
    parser.add_argument("inputs", nargs="*", default=[str(path) for path in DEFAULT_INPUTS])
    args = parser.parse_args()
    frame = merge_datasets(
        tuple(args.inputs),
        args.output,
        max_per_label=args.max_per_label,
    )
    print_distribution(frame, args.output)


if __name__ == "__main__":
    main()
