from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.datasets import profile_csv_dataset


def _write_csv(path: Path, rows: int = 5) -> None:
    lines = ["timestamp,value,temp,category,label"]
    for index in range(rows):
        lines.append(f"2026-01-{(index % 9) + 1:02d},{index * 1.5},{20 + index},type-{index % 3},{index % 2}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_delimited(path: Path, delimiter: str, rows: int = 5) -> None:
    lines = [delimiter.join(["timestamp", "value", "temp", "category"])]
    for index in range(rows):
        lines.append(delimiter.join([f"2026-01-{(index % 9) + 1:02d}", str(index), str(20 + index), f"type-{index % 2}"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_dataset_profiler_detects_date_numeric_and_categorical_columns(tmp_path: Path):
    csv_path = tmp_path / "events.csv"
    _write_csv(csv_path)

    profile = profile_csv_dataset(csv_path, row_count_override=35_000)

    assert "timestamp" in profile.detected_date_columns
    assert "value" in profile.detected_numeric_columns
    assert "category" in profile.detected_categorical_columns
    assert profile.suitability.assignment_2_suitable is True
    assert profile.detected_format == "csv"
    assert profile.detected_delimiter == ","


def test_dataset_profiler_supports_semicolon_txt(tmp_path: Path):
    path = tmp_path / "events.txt"
    _write_delimited(path, ";")

    profile = profile_csv_dataset(path, row_count_override=35_000)

    assert profile.detected_format == "txt"
    assert profile.detected_delimiter == ";"
    assert "timestamp" in profile.detected_date_columns


def test_dataset_profiler_supports_tab_tsv(tmp_path: Path):
    path = tmp_path / "events.tsv"
    _write_delimited(path, "\t")

    profile = profile_csv_dataset(path, row_count_override=35_000)

    assert profile.detected_format == "tsv"
    assert profile.detected_delimiter == "\t"
    assert "value" in profile.detected_numeric_columns


def test_dataset_profiler_supports_pipe_txt(tmp_path: Path):
    path = tmp_path / "events.txt"
    _write_delimited(path, "|")

    profile = profile_csv_dataset(path, row_count_override=35_000)

    assert profile.detected_delimiter == "|"
    assert profile.column_count == 4


def test_dataset_profiler_rejects_unsupported_extension_cleanly(tmp_path: Path):
    path = tmp_path / "events.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=".csv, .txt, .tsv"):
        profile_csv_dataset(path)


def test_dataset_profiler_warns_for_malformed_rows(tmp_path: Path):
    path = tmp_path / "bad.txt"
    path.write_text("timestamp|value\n2026-01-01|1|extra\n2026-01-02\n", encoding="utf-8")

    profile = profile_csv_dataset(path)

    assert profile.detected_delimiter == "|"
    assert profile.warnings
    assert any("Malformed row" in warning for warning in profile.warnings)


def test_dataset_profiler_catches_missing_requirements(tmp_path: Path):
    csv_path = tmp_path / "tiny.csv"
    csv_path.write_text("name\nalpha\nbeta\n", encoding="utf-8")

    profile = profile_csv_dataset(csv_path)

    assert profile.suitability.assignment_1_suitable is False
    assert profile.suitability.assignment_2_suitable is False
    assert profile.suitability.assignment_3_suitable is False
    assert profile.suitability.reasons


def test_dataset_profiler_recommends_assignment(tmp_path: Path):
    csv_path = tmp_path / "stream.csv"
    _write_csv(csv_path)

    profile = profile_csv_dataset(csv_path, row_count_override=21_000)

    assert profile.suitability.recommended_assignment_use == "assignment_1"


def test_dataset_profiler_refuses_secret_like_files(tmp_path: Path):
    secret = tmp_path / "credentials.csv"
    _write_csv(secret)

    with pytest.raises(ValueError):
        profile_csv_dataset(secret)


def test_dataset_profiler_output_is_deterministic(tmp_path: Path):
    csv_path = tmp_path / "events.csv"
    _write_csv(csv_path)

    first = profile_csv_dataset(csv_path, row_count_override=35_000).model_dump(mode="json")
    second = profile_csv_dataset(csv_path, row_count_override=35_000).model_dump(mode="json")

    assert first == second
