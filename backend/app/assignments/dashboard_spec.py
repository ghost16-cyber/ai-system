from __future__ import annotations

from backend.app.assignments.schemas import DashboardChartSpec, DashboardSpec
from backend.app.datasets.schemas import DatasetProfile
from backend.app.assignments.dataset_mapper import map_dataset_columns, require_resolved_semantic_mapping


def generate_dashboard_spec(
    assignment_number: int,
    *,
    dataset_profile: DatasetProfile | None = None,
) -> DashboardSpec:
    cols = _columns(dataset_profile)
    if assignment_number == 1:
        return DashboardSpec(
            assignment_number=1,
            dashboard_title="Assignment 1 Kafka / InfluxDB / Grafana Dashboard",
            dashboard_type="Grafana",
            data_source="InfluxDB measurement populated by Kafka consumer",
            required_filters=[cols["category"], "time range"],
            kpi_cards=["Total streamed records", "Latest value", "Average value", "Threshold breaches"],
            charts=[
                _chart("a1-trend", "Time-series trend", "time_series", [cols["date"], cols["numeric"]], "Show metric movement over time."),
                _chart("a1-category", "Category breakdown", "bar", [cols["category"], "event_count"], "Compare record volume by category."),
                _chart("a1-grouped", "Grouped metric", "grouped_bar", [cols["category"], cols["numeric"]], "Compare metric values across groups."),
                _chart("a1-summary", "Summary statistic", "stat", [cols["numeric"]], "Show min/max/average summary."),
            ],
            tables=["Recent streamed records"],
            refresh_behavior="Grafana auto-refresh every 5-10 seconds while services are running.",
            screenshot_requirements=["Grafana dashboard with 4+ panels", "InfluxDB Data Explorer query", "Docker containers running"],
            implementation_notes=["Use real dashboard screenshots only after data is flowing.", "Do not invent panel values."],
        )
    if assignment_number == 2:
        return DashboardSpec(
            assignment_number=2,
            dashboard_title="Assignment 2 Snowflake / Streamlit Dashboard",
            dashboard_type="Streamlit",
            data_source="Snowflake query source or exported aggregate files",
            required_filters=[cols["category"], "date range"],
            kpi_cards=["Rows loaded", "Average metric", "Distinct categories"],
            charts=[
                _chart("a2-line", "Metric over time", "line", [cols["date"], cols["numeric"]], "Track change over time."),
                _chart("a2-bar", "Category comparison", "bar", [cols["category"], cols["numeric"]], "Compare groups."),
                _chart("a2-scatter", "Numeric relationship", "scatter", [cols["numeric"], cols["numeric_two"]], "Explore relationships."),
            ],
            tables=["Filtered Snowflake result table"],
            refresh_behavior="Refresh when the user changes filters or clicks reload.",
            screenshot_requirements=["Streamlit dashboard", "Snowflake worksheet validation"],
            implementation_notes=["Use parameterized Snowflake reads or prepared aggregate output.", "Show placeholders until real query results exist."],
        )
    if assignment_number == 3:
        return DashboardSpec(
            assignment_number=3,
            dashboard_title="Assignment 3 Live Redis / Streamlit Dashboard",
            dashboard_type="Streamlit live",
            data_source="Redis latest_metrics and recent_records keys",
            required_filters=[cols["category"], "refresh interval"],
            kpi_cards=["Live event count", "Latest window average", "Classification count"],
            charts=[
                _chart("a3-window", "Window average over time", "line", ["window_start", cols["numeric"]], "Show streaming windows."),
                _chart("a3-late", "Late data over time", "area", ["window_start", "late_record_count"], "Support watermark evidence."),
                _chart("a3-class", "Classification breakdown", "bar", [cols["classification"], "event_count"], "Show severity/class distribution."),
            ],
            tables=["Recent records feed"],
            refresh_behavior="Auto-refresh every 2-5 seconds from Redis without rerunning commands automatically.",
            screenshot_requirements=["Streamlit live dashboard", "Redis CLI output", "Watermark/query plan/logs"],
            implementation_notes=["Keep Redis reads lightweight.", "Do not claim live values until captured in screenshots."],
        )
    raise ValueError("Assignment number must be 1, 2, or 3.")


def _chart(chart_id: str, title: str, chart_type: str, fields: list[str], purpose: str) -> DashboardChartSpec:
    return DashboardChartSpec(chart_id=chart_id, title=title, chart_type=chart_type, data_fields=fields, purpose=purpose)


def _columns(profile: DatasetProfile | None) -> dict[str, str]:
    mapping = require_resolved_semantic_mapping(profile) if profile is not None else map_dataset_columns(None)
    date = mapping.timestamp_column.column if profile else "TIMESTAMP_COLUMN"
    numeric = mapping.primary_numeric_indicator.column if profile else "NUMERIC_COLUMN"
    numeric_two = profile.detected_numeric_columns[1] if profile and len(profile.detected_numeric_columns) > 1 else numeric
    category = mapping.category_grouping_column.column if profile else "CATEGORY_COLUMN"
    classification = next((col for col in (profile.detected_numeric_columns if profile else []) if col.lower() in {"label", "target", "severity", "indicator"}), "CLASSIFICATION_COLUMN")
    return {"date": date, "numeric": numeric, "numeric_two": numeric_two, "category": category, "classification": classification}
