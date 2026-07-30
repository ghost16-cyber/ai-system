from __future__ import annotations

import argparse
import html
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd


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

TAG_LABEL_RULES: dict[str, set[str]] = {
    "backend": {
        "fastapi",
        "django",
        "flask",
        "api",
        "rest",
        "sqlalchemy",
        "postgresql",
        "mysql",
        "sqlite",
        "database",
        "backend",
    },
    "frontend": {
        "reactjs",
        "react",
        "vite",
        "typescript",
        "javascript",
        "html",
        "css",
        "frontend",
        "vue.js",
        "angular",
    },
    "debugging": {
        "error",
        "exception",
        "traceback",
        "bug",
        "crash",
        "failed",
        "not-working",
        "debugging",
    },
    "testing": {
        "pytest",
        "unittest",
        "jest",
        "testing",
        "unit-testing",
        "mock",
        "coverage",
    },
    "rag": {
        "embedding",
        "vector-database",
        "langchain",
        "chromadb",
        "qdrant",
        "faiss",
        "retrieval",
        "semantic-search",
    },
    "training": {
        "scikit-learn",
        "sklearn",
        "machine-learning",
        "classification",
        "training",
        "dataset",
        "accuracy",
        "model",
        "fit",
    },
    "runtime": {
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
    },
}

KEYWORD_LABEL_RULES = TAG_LABEL_RULES
GENERAL_TERMS = (
    "how to",
    "what is",
    "why does",
    "explain",
    "best way",
    "approach",
    "design",
    "planning",
)

DEFAULT_INPUT_DIR = Path("data/stack")
DEFAULT_OUTPUT_PATH = Path("data/specialists/intent_examples_stacklite_seed.csv")

MAX_TEXT_CHARS = 360


def build_dataset(
    input_dir: str | Path = DEFAULT_INPUT_DIR,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    max_per_label: int = 100,
    chunksize: int = 200_000,
    candidate_multiplier: int = 8,
) -> pd.DataFrame:
    input_root = Path(input_dir)
    questions_path = input_root / "questions.csv"
    tags_path = input_root / "question_tags.csv"
    output = Path(output_path)

    if not questions_path.exists():
        raise FileNotFoundError(f"Missing questions CSV: {questions_path}")
    if not tags_path.exists():
        raise FileNotFoundError(f"Missing question_tags CSV: {tags_path}")

    tag_candidates = collect_tag_candidates(
        tags_path,
        max_per_label=max_per_label,
        chunksize=chunksize,
        candidate_multiplier=candidate_multiplier,
    )
    rows = collect_question_rows(
        questions_path,
        tag_candidates,
        max_per_label=max_per_label,
        chunksize=chunksize,
    )
    frame = pd.DataFrame(rows, columns=["user_message", "final_label", "label_status", "source"])
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return frame


def collect_tag_candidates(
    tags_path: str | Path,
    *,
    max_per_label: int,
    chunksize: int,
    candidate_multiplier: int,
) -> dict[str, set[str]]:
    candidate_limit = max_per_label * max(1, candidate_multiplier)
    candidates: dict[str, set[str]] = {label: set() for label in ASTRA_LABELS}
    tags_by_id: dict[str, set[str]] = defaultdict(set)

    for chunk in pd.read_csv(tags_path, chunksize=chunksize, dtype=str, keep_default_na=False):
        id_col = find_column(chunk.columns, ("id", "questionid", "question_id"))
        tag_col = find_column(chunk.columns, ("tag", "tags"))
        if id_col is None or tag_col is None:
            raise ValueError("question_tags.csv must contain question id and tag columns.")

        for question_id, tag in zip(chunk[id_col], chunk[tag_col]):
            normalized_id = str(question_id).strip()
            normalized_tag = normalize_tag(tag)
            if not normalized_id or not normalized_tag:
                continue
            labels = labels_from_tags([normalized_tag])
            if labels:
                for label in labels:
                    if len(candidates[label]) < candidate_limit:
                        candidates[label].add(normalized_id)
                        tags_by_id[normalized_id].add(normalized_tag)
            elif len(candidates["general"]) < candidate_limit:
                candidates["general"].add(normalized_id)
                tags_by_id[normalized_id].add(normalized_tag)

        if all(len(candidates[label]) >= candidate_limit for label in ASTRA_LABELS):
            break

    # Second pass is avoided intentionally; keep the join set compact. Merge all
    # tags observed for selected IDs during the first streaming pass.
    return {question_id: tags for question_id, tags in tags_by_id.items() if question_id in set().union(*candidates.values())}


def collect_question_rows(
    questions_path: str | Path,
    tag_candidates: dict[str, set[str]],
    *,
    max_per_label: int,
    chunksize: int,
) -> list[dict[str, str]]:
    if not tag_candidates:
        return []

    remaining_ids = set(tag_candidates)
    counts: Counter[str] = Counter()
    seen_messages: set[str] = set()
    rows: list[dict[str, str]] = []

    for chunk in pd.read_csv(questions_path, chunksize=chunksize, dtype=str, keep_default_na=False):
        id_col = find_column(chunk.columns, ("id", "questionid", "question_id"))
        if id_col is None:
            raise ValueError("questions.csv must contain a question id column.")

        selected = chunk[chunk[id_col].astype(str).isin(remaining_ids)]
        for _, row in selected.iterrows():
            question_id = str(row[id_col]).strip()
            tags = tag_candidates.get(question_id, set())
            message = message_from_question(row, tags)
            normalized = normalize_message(message)
            if not normalized or normalized in seen_messages:
                continue

            label = label_for_example(tags, normalized)
            if label not in ASTRA_LABELS or counts[label] >= max_per_label:
                continue

            seen_messages.add(normalized)
            counts[label] += 1
            rows.append(
                {
                    "user_message": message,
                    "final_label": label,
                    "label_status": "confirmed",
                    "source": "stackoverflow",
                }
            )
        remaining_ids -= set(selected[id_col].astype(str))
        if not remaining_ids or all(counts[label] >= max_per_label for label in ASTRA_LABELS):
            break

    return rows


def message_from_question(row: pd.Series, tags: Iterable[str]) -> str:
    title_col = find_column(row.index, ("title", "questiontitle", "question_title"))
    body_col = find_column(row.index, ("body", "question", "questiontext", "question_text", "text"))
    title = str(row.get(title_col, "")).strip() if title_col else ""
    body = str(row.get(body_col, "")).strip() if body_col else ""
    if title:
        return clean_text(title)
    if body:
        return clean_text(body)
    return synthetic_message_from_tags(tags)


def clean_text(value: str) -> str:
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<pre.*?>.*?</pre>", " ", text)
    text = re.sub(r"(?is)<code.*?>.*?</code>", " ", text)
    text = re.sub(r"(?is)```.*?```", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rsplit(" ", 1)[0].strip()
    return text


def label_for_example(tags: Iterable[str], message: str) -> str:
    tag_labels = labels_from_tags(tags)
    if tag_labels:
        return tag_labels[0]
    keyword_labels = labels_from_keywords(message)
    if keyword_labels:
        return keyword_labels[0]
    lowered = message.lower()
    if any(term in lowered for term in GENERAL_TERMS):
        return "general"
    return "general" if message.startswith("How do I work with ") else "unknown"


def labels_from_tags(tags: Iterable[str]) -> list[str]:
    tag_set = {normalize_tag(tag) for tag in tags}
    labels: list[str] = []
    for label, markers in TAG_LABEL_RULES.items():
        if tag_set & markers:
            labels.append(label)
    return labels


def labels_from_keywords(message: str) -> list[str]:
    lowered = message.lower()
    labels: list[str] = []
    for label, markers in KEYWORD_LABEL_RULES.items():
        if any(marker in lowered for marker in markers):
            labels.append(label)
    return labels


def normalize_message(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def normalize_tag(tag: str) -> str:
    return str(tag).strip().lower()


def synthetic_message_from_tags(tags: Iterable[str]) -> str:
    selected = [tag for tag in sorted({normalize_tag(tag) for tag in tags}) if tag]
    if not selected:
        return ""
    readable = ", ".join(selected[:4])
    return f"How do I work with {readable}?"


def find_column(columns: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {str(column).strip().lower().replace("-", "_"): str(column) for column in columns}
    for candidate in candidates:
        key = candidate.strip().lower().replace("-", "_")
        if key in normalized:
            return normalized[key]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Astra intent-router seed data from StackLite CSVs.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--max-per-label", type=int, default=100)
    parser.add_argument("--chunksize", type=int, default=200_000)
    args = parser.parse_args()

    frame = build_dataset(
        args.input_dir,
        args.output,
        max_per_label=args.max_per_label,
        chunksize=args.chunksize,
    )
    distribution = frame["final_label"].value_counts().sort_index().to_dict() if not frame.empty else {}
    print(f"Wrote {len(frame)} examples to {args.output}")
    print("Final label distribution:")
    for label, count in distribution.items():
        print(f"- {label}: {count}")


if __name__ == "__main__":
    main()
