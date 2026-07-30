from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_intent_dataset_from_huggingface_stackoverflow import (
    build_dataset,
    clean_text,
    collect_rows,
    label_for_record,
    load_stackoverflow_rows,
    message_from_record,
)


def test_hf_loader_falls_back_to_next_dataset_name():
    calls: list[tuple[str, str | None]] = []

    def fake_loader(name: str, **kwargs):
        calls.append((name, kwargs.get("split")))
        if name == "preferred":
            raise RuntimeError("missing preferred")
        return [{"title": "How to build a FastAPI endpoint?", "body": ""}]

    rows = list(
        load_stackoverflow_rows(
            dataset_name="preferred",
            fallback_dataset_names=("fallback",),
            split="train",
            loader=fake_loader,
        )
    )

    assert calls == [("preferred", "train"), ("fallback", "train")]
    assert rows[0]["title"] == "How to build a FastAPI endpoint?"


def test_hf_text_cleaning_and_short_title_body_fallback():
    raw = (
        "# Heading <p>Hello <strong>world</strong></p>"
        "<pre><code>secret_code()</code></pre>"
        "See [docs](https://example.com) and person@example.com `inline`"
    )

    cleaned = clean_text(raw)
    message = message_from_record(
        {
            "title": "Help",
            "body": "<p>How do I fix a pytest coverage report in Python?</p>",
        }
    )

    assert "<" not in cleaned
    assert "secret_code" not in cleaned
    assert "https://example.com" not in cleaned
    assert "person@example.com" not in cleaned
    assert "Hello world" in cleaned
    assert "inline" in cleaned
    assert message == "Help How do I fix a pytest coverage report in Python?"


def test_hf_keyword_mapping():
    assert label_for_record({"title": "FastAPI SQLite endpoint error", "body": ""}) == "backend"
    assert label_for_record({"title": "React CSS component layout", "body": ""}) == "frontend"
    assert label_for_record({"title": "pytest mock coverage question", "body": ""}) == "testing"
    assert label_for_record({"title": "LangChain FAISS semantic search retrieval", "body": ""}) == "rag"
    assert label_for_record({"title": "CUDA GPU memory with PyTorch", "body": ""}) == "runtime"
    assert label_for_record({"title": "What is the best way to design this?", "body": ""}) == "general"


def test_hf_output_schema_and_source(tmp_path: Path):
    records = [
        {"title": "How to build a FastAPI endpoint?", "body": ""},
        {"title": "How to style a React component?", "body": ""},
    ]

    def fake_loader(*args, **kwargs):
        return records

    output = tmp_path / "hf_intent.csv"
    frame = build_dataset(output, loader=fake_loader, max_per_label=10)

    assert list(frame.columns) == ["user_message", "final_label", "label_status", "source"]
    assert output.exists()
    saved = pd.read_csv(output)
    assert list(saved.columns) == ["user_message", "final_label", "label_status", "source"]
    assert set(saved["source"]) == {"huggingface_stackoverflow"}
    assert set(saved["label_status"]) == {"confirmed"}


def test_hf_deduplicates_normalized_messages_and_balances():
    records = []
    for index in range(1, 7):
        records.append({"title": f"How to build FastAPI endpoint {index}?", "body": ""})
    records.append({"title": "  how   to build fastapi endpoint 1? ", "body": ""})
    for index in range(1, 7):
        records.append({"title": f"How to style React component {index}?", "body": ""})

    rows = collect_rows(records, max_per_label=3)
    distribution = {}
    for row in rows:
        distribution[row["final_label"]] = distribution.get(row["final_label"], 0) + 1

    assert distribution == {"backend": 3, "frontend": 3}
    assert len({row["user_message"].strip().lower() for row in rows}) == 6
