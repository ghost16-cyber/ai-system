from __future__ import annotations

import argparse
import html
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pandas as pd


PREFERRED_DATASET = "pacovaldez/stackoverflow-questions"
FALLBACK_DATASETS = ("pacovaldez/stackoverflow-questions-2016",)
DEFAULT_OUTPUT_PATH = Path("data/specialists/intent_examples_hf_stackoverflow_seed.csv")

ASTRA_LABELS = (
    "backend",
    "frontend",
    "debugging",
    "testing",
    "rag",
    "training",
    "runtime",
    "general",
)

KEYWORD_LABEL_RULES: dict[str, tuple[str, ...]] = {
    "backend": (
        "fastapi",
        "django",
        "flask",
        " api ",
        " rest ",
        "sqlalchemy",
        "postgresql",
        "mysql",
        "sqlite",
        "database",
        "backend",
        "endpoint",
    ),
    "frontend": (
        "reactjs",
        "react",
        "vite",
        "typescript",
        "javascript",
        " html ",
        " css ",
        "frontend",
        "vue.js",
        "angular",
        "component",
    ),
    "debugging": (
        "error",
        "exception",
        "traceback",
        "bug",
        "crash",
        "failed",
        "not working",
        "not-working",
        "debugging",
    ),
    "testing": (
        "pytest",
        "unittest",
        "jest",
        "testing",
        "unit testing",
        "unit-testing",
        "mock",
        "coverage",
    ),
    "rag": (
        "embedding",
        "vector database",
        "vector-database",
        "langchain",
        "chromadb",
        "qdrant",
        "faiss",
        "retrieval",
        "semantic search",
        "semantic-search",
    ),
    "training": (
        "scikit-learn",
        "sklearn",
        "machine learning",
        "machine-learning",
        "classification",
        "training",
        "dataset",
        "accuracy",
        "model",
        " fit ",
    ),
    "runtime": (
        "cuda",
        "gpu",
        "pytorch",
        "ollama",
        "node.js",
        "npm",
        "uvicorn",
        "memory",
        "performance",
        "server",
        "deployment",
    ),
}

GENERAL_TERMS = (
    "how to",
    "what is",
    "why does",
    "best way",
    "explain",
    "difference between",
    "approach",
    "design",
)

MIN_MESSAGE_CHARS = 15
MAX_MESSAGE_CHARS = 220


def build_dataset(
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    dataset_name: str = PREFERRED_DATASET,
    fallback_dataset_names: tuple[str, ...] = FALLBACK_DATASETS,
    split: str | None = None,
    max_per_label: int = 100,
    max_rows: int | None = None,
    loader: Callable[..., Any] | None = None,
) -> pd.DataFrame:
    rows = collect_rows(
        load_stackoverflow_rows(
            dataset_name=dataset_name,
            fallback_dataset_names=fallback_dataset_names,
            split=split,
            loader=loader,
        ),
        max_per_label=max_per_label,
        max_rows=max_rows,
    )
    frame = pd.DataFrame(rows, columns=["user_message", "final_label", "label_status", "source"])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return frame


def load_stackoverflow_rows(
    *,
    dataset_name: str = PREFERRED_DATASET,
    fallback_dataset_names: tuple[str, ...] = FALLBACK_DATASETS,
    split: str | None = None,
    loader: Callable[..., Any] | None = None,
) -> Iterable[dict[str, Any]]:
    active_loader = loader or _load_dataset
    dataset_names = (dataset_name, *fallback_dataset_names)
    split_candidates = (split,) if split else ("train", "validation", "test")
    errors: list[str] = []
    for name in dataset_names:
        for split_name in split_candidates:
            try:
                loaded = active_loader(name, split=split_name, streaming=True)
                return _iter_rows(loaded)
            except Exception as error:
                errors.append(f"{name}/{split_name}: {error}")
        if split is not None:
            continue
        try:
            loaded = active_loader(name, streaming=True)
            return _iter_rows(_first_split(loaded))
        except Exception as error:
            errors.append(f"{name}/default: {error}")
    raise RuntimeError("Could not load a Hugging Face Stack Overflow dataset. " + " | ".join(errors))


def collect_rows(
    records: Iterable[dict[str, Any]],
    *,
    max_per_label: int,
    max_rows: int | None = None,
) -> list[dict[str, str]]:
    counts: Counter[str] = Counter()
    seen_messages: set[str] = set()
    rows: list[dict[str, str]] = []

    for index, record in enumerate(records):
        if max_rows is not None and index >= max_rows:
            break
        message = message_from_record(record)
        if not message:
            continue
        normalized = normalize_message(message)
        if normalized in seen_messages:
            continue
        label = label_for_record(record)
        if label not in ASTRA_LABELS or counts[label] >= max_per_label:
            continue
        seen_messages.add(normalized)
        counts[label] += 1
        rows.append(
            {
                "user_message": message,
                "final_label": label,
                "label_status": "confirmed",
                "source": "huggingface_stackoverflow",
            }
        )
        if all(counts[label] >= max_per_label for label in ASTRA_LABELS):
            break
    return rows


def message_from_record(record: dict[str, Any]) -> str:
    title = clean_text(first_present(record, ("title", "Title", "question_title")))
    body = clean_text(first_present(record, ("body", "Body", "question", "text", "content")))
    if len(title) >= MIN_MESSAGE_CHARS:
        return fit_message(title)
    combined = clean_text(f"{title} {body}".strip())
    return fit_message(combined)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<pre.*?>.*?</pre>", " ", text)
    text = re.sub(r"(?is)<code.*?>.*?</code>", " ", text)
    text = re.sub(r"(?is)```.*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", " ", text)
    text = re.sub(r"(?m)^\s{0,3}>\s?", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"[*_~]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fit_message(message: str) -> str:
    text = clean_text(message)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS].rsplit(" ", 1)[0].strip()
    if len(text) < MIN_MESSAGE_CHARS:
        return ""
    return text


def label_for_record(record: dict[str, Any]) -> str:
    title = clean_text(first_present(record, ("title", "Title", "question_title")))
    body = clean_text(first_present(record, ("body", "Body", "question", "text", "content")))
    searchable = f" {title} {body} ".lower()
    for label, keywords in KEYWORD_LABEL_RULES.items():
        if any(keyword in searchable for keyword in keywords):
            return label
    if any(term in searchable for term in GENERAL_TERMS):
        return "general"
    return "unknown"


def normalize_message(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def first_present(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return ""


def _load_dataset(*args: Any, **kwargs: Any) -> Any:
    from datasets import load_dataset

    return load_dataset(*args, **kwargs)


def _iter_rows(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        value = _first_split(value)
    for row in value:
        if isinstance(row, dict):
            yield row


def _first_split(value: Any) -> Any:
    if isinstance(value, dict):
        for split_name in ("train", "validation", "test"):
            if split_name in value:
                return value[split_name]
        return next(iter(value.values()))
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Astra intent-router seed data from a Hugging Face Stack Overflow dataset."
    )
    parser.add_argument("--dataset", default=PREFERRED_DATASET)
    parser.add_argument("--fallback-dataset", action="append", default=list(FALLBACK_DATASETS))
    parser.add_argument("--split", default=None)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--max-per-label", type=int, default=100)
    parser.add_argument("--max-rows", type=int, default=None)
    args = parser.parse_args()

    frame = build_dataset(
        args.output,
        dataset_name=args.dataset,
        fallback_dataset_names=tuple(args.fallback_dataset),
        split=args.split,
        max_per_label=args.max_per_label,
        max_rows=args.max_rows,
    )
    distribution = frame["final_label"].value_counts().sort_index().to_dict() if not frame.empty else {}
    print(f"Wrote {len(frame)} examples to {args.output}")
    print("Final label distribution:")
    for label, count in distribution.items():
        print(f"- {label}: {count}")


if __name__ == "__main__":
    main()
