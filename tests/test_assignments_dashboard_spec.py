from __future__ import annotations

from pathlib import Path

from backend.app.assignments.dashboard_spec import generate_dashboard_spec
from backend.app.datasets import profile_csv_dataset


def _profile(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text("event_time,value,score,region,label\n2026-01-01,10,3,north,1\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_assignment_1_has_four_grafana_panels(tmp_path: Path):
    spec = generate_dashboard_spec(1, dataset_profile=_profile(tmp_path))
    assert spec.dashboard_type == "Grafana"
    assert len(spec.charts) >= 4


def test_assignment_2_has_filter_kpis_three_chart_types_and_table(tmp_path: Path):
    spec = generate_dashboard_spec(2, dataset_profile=_profile(tmp_path))
    assert spec.required_filters
    assert spec.kpi_cards
    assert len({chart.chart_type for chart in spec.charts}) >= 3
    assert spec.tables
    assert "Snowflake" in spec.data_source


def test_assignment_3_has_auto_refresh_redis_and_recent_feed(tmp_path: Path):
    spec = generate_dashboard_spec(3, dataset_profile=_profile(tmp_path))
    assert "Auto-refresh" in spec.refresh_behavior
    assert "Redis" in spec.data_source
    assert "Recent records feed" in spec.tables


def test_dashboard_screenshots_are_included(tmp_path: Path):
    assert generate_dashboard_spec(1, dataset_profile=_profile(tmp_path)).screenshot_requirements
    assert generate_dashboard_spec(3, dataset_profile=_profile(tmp_path)).screenshot_requirements


def test_dashboard_spec_is_deterministic(tmp_path: Path):
    profile = _profile(tmp_path)
    assert generate_dashboard_spec(2, dataset_profile=profile).model_dump(mode="json") == generate_dashboard_spec(2, dataset_profile=profile).model_dump(mode="json")
