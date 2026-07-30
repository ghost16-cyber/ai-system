from __future__ import annotations

from pathlib import Path

from backend.app.assignments.dataset_mapper import map_dataset_columns
from backend.app.datasets import profile_csv_dataset


def _profile(tmp_path: Path, header: str, row: str, rows: int = 5):
    path = tmp_path / "events.csv"
    path.write_text(header + "\n" + "\n".join(row for _ in range(rows)) + "\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_dataset_mapper_selects_date_column_as_timestamp(tmp_path: Path):
    mapping = map_dataset_columns(_profile(tmp_path, "event_time,value,site", "2026-01-01,10,A"))

    assert mapping.timestamp_column.column == "event_time"
    assert mapping.timestamp_column.placeholder is False


def test_dataset_mapper_selects_numeric_indicator(tmp_path: Path):
    mapping = map_dataset_columns(_profile(tmp_path, "event_time,temp,humidity,site", "2026-01-01,21.5,44,A"))

    assert mapping.primary_numeric_indicator.column == "temp"
    assert "humidity" in [item.column for item in mapping.secondary_numeric_fields]


def test_dataset_mapper_selects_categorical_column(tmp_path: Path):
    mapping = map_dataset_columns(_profile(tmp_path, "event_time,value,site", "2026-01-01,10,A"))

    assert mapping.category_grouping_column.column == "site"
    assert mapping.dashboard_filter_column.column == "site"


def test_dataset_mapper_missing_date_column_produces_warning(tmp_path: Path):
    mapping = map_dataset_columns(_profile(tmp_path, "value,site", "10,A"))

    assert mapping.timestamp_column.placeholder is True
    assert any("timestamp" in warning.lower() for warning in mapping.warnings)


def test_dataset_mapper_missing_numeric_column_produces_warning(tmp_path: Path):
    mapping = map_dataset_columns(_profile(tmp_path, "event_time,site", "2026-01-01,A"))

    assert mapping.primary_numeric_indicator.placeholder is True
    assert any("numeric" in warning.lower() for warning in mapping.warnings)


def test_dataset_mapper_output_is_deterministic(tmp_path: Path):
    profile = _profile(tmp_path, "event_time,value,site", "2026-01-01,10,A")

    first = map_dataset_columns(profile).model_dump(mode="json")
    second = map_dataset_columns(profile).model_dump(mode="json")

    assert first == second
