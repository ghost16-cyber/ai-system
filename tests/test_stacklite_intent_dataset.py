from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_intent_dataset_from_stacklite import (
    build_dataset,
    clean_text,
    label_for_example,
    labels_from_tags,
    normalize_message,
)


def test_stacklite_output_csv_schema(tmp_path: Path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "questions.csv").write_text(
        "Id,Title,Body\n"
        "1,How do I build a FastAPI endpoint?,ignored\n"
        "2,How do I style a React component?,ignored\n",
        encoding="utf-8",
    )
    (stack / "question_tags.csv").write_text(
        "Id,Tag\n"
        "1,fastapi\n"
        "2,reactjs\n",
        encoding="utf-8",
    )

    output = tmp_path / "intent.csv"
    frame = build_dataset(stack, output, max_per_label=10, chunksize=1)

    assert list(frame.columns) == ["user_message", "final_label", "label_status", "source"]
    assert output.exists()
    saved = pd.read_csv(output)
    assert list(saved.columns) == ["user_message", "final_label", "label_status", "source"]
    assert set(saved["source"]) == {"stackoverflow"}
    assert set(saved["label_status"]) == {"confirmed"}


def test_stacklite_text_cleaning_removes_html_code_urls_email_and_long_text():
    raw = (
        "<p>Hello <strong>world</strong></p>"
        "<pre><code>secret_code()</code></pre>"
        " See https://example.com and person@example.com "
        + ("word " * 120)
    )

    cleaned = clean_text(raw)

    assert "<" not in cleaned
    assert "secret_code" not in cleaned
    assert "https://example.com" not in cleaned
    assert "person@example.com" not in cleaned
    assert "Hello world" in cleaned
    assert len(cleaned) <= 360


def test_stacklite_tag_to_label_mapping():
    assert labels_from_tags(["fastapi"]) == ["backend"]
    assert labels_from_tags(["reactjs"]) == ["frontend"]
    assert labels_from_tags(["pytest"]) == ["testing"]
    assert labels_from_tags(["langchain"]) == ["rag"]
    assert label_for_example(["cuda"], "How do I fix CUDA memory?") == "runtime"
    assert label_for_example([], "What is the best way to design this?") == "general"


def test_stacklite_deduplicates_by_normalized_user_message(tmp_path: Path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "questions.csv").write_text(
        "Id,Title\n"
        "1,How do I build a FastAPI endpoint?\n"
        "2,  how   do i build a fastapi endpoint?  \n",
        encoding="utf-8",
    )
    (stack / "question_tags.csv").write_text(
        "Id,Tag\n"
        "1,fastapi\n"
        "2,fastapi\n",
        encoding="utf-8",
    )

    frame = build_dataset(stack, tmp_path / "intent.csv", max_per_label=10, chunksize=1)

    assert len(frame) == 1
    assert normalize_message(frame.iloc[0]["user_message"]) == "how do i build a fastapi endpoint?"


def test_stacklite_balances_output_to_max_per_label(tmp_path: Path):
    stack = tmp_path / "stack"
    stack.mkdir()
    questions = ["Id,Title"]
    tags = ["Id,Tag"]
    for index in range(1, 7):
        questions.append(f"{index},How do I build FastAPI endpoint {index}?")
        tags.append(f"{index},fastapi")
    for index in range(7, 13):
        questions.append(f"{index},How do I style React component {index}?")
        tags.append(f"{index},reactjs")
    (stack / "questions.csv").write_text("\n".join(questions) + "\n", encoding="utf-8")
    (stack / "question_tags.csv").write_text("\n".join(tags) + "\n", encoding="utf-8")

    frame = build_dataset(stack, tmp_path / "intent.csv", max_per_label=3, chunksize=2)
    distribution = frame["final_label"].value_counts().to_dict()

    assert distribution == {"backend": 3, "frontend": 3}
