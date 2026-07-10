from __future__ import annotations

import re

from backend.app.assignments.schemas import AssignmentDatasetMapping, DatasetMappingSuggestion
from backend.app.datasets.schemas import DatasetProfile


def map_dataset_columns(profile: DatasetProfile | None) -> AssignmentDatasetMapping:
    warnings: list[str] = []
    if profile is None:
        warnings.append("Dataset has not been profiled; placeholders are used.")
        return _placeholder_mapping(warnings)

    timestamp = _suggestion(
        _first(profile.detected_date_columns),
        "timestamp/date columns are suitable for Kafka event time, Spark windows, and dashboard time filters.",
        "TIMESTAMP_COLUMN",
        warnings,
        "No timestamp/date column was detected; choose one manually before streaming or window analysis.",
    )
    primary = _suggestion(
        _first(profile.detected_numeric_columns),
        "numeric columns are suitable as indicators, aggregation measures, and dashboard KPI values.",
        "NUMERIC_COLUMN",
        warnings,
        "No numeric column was detected; choose or derive one before profiling assignment suitability.",
    )
    secondary = [
        DatasetMappingSuggestion(
            column=column,
            reason="secondary numeric fields can support extra KPIs, comparisons, and quality checks.",
        )
        for column in profile.detected_numeric_columns[1:5]
    ]
    category = _suggestion(
        _first(profile.detected_categorical_columns),
        "categorical columns are suitable for grouping, filters, Redis keys, and dashboard dimensions.",
        "CATEGORY_COLUMN",
        warnings,
        "No categorical column was detected; choose a grouping field manually for dashboards and aggregations.",
    )
    threshold = (
        f"Use the median or domain-approved threshold of `{primary.column}` after inspecting the real distribution."
        if not primary.placeholder
        else "Choose a numeric indicator first, then set a threshold from the real distribution."
    )
    table_base = _safe_identifier(profile.columns[0] if profile.columns else "events")
    spark_columns = [item for item in [category.column, primary.column, timestamp.column] if not item.isupper()]
    return AssignmentDatasetMapping(
        dataset_path=profile.dataset_path,
        timestamp_column=timestamp,
        primary_numeric_indicator=primary,
        secondary_numeric_fields=secondary,
        category_grouping_column=category,
        classification_threshold_idea=threshold,
        dashboard_filter_column=category,
        spark_aggregation_columns=spark_columns,
        snowflake_table_names=[f"RAW_{table_base}", f"CURATED_{table_base}", f"AGG_{table_base}"],
        redis_key_patterns=[
            f"latest:{_safe_identifier(category.column).lower()}:{{value}}",
            f"metric:{_safe_identifier(primary.column).lower()}",
        ],
        warnings=warnings,
        placeholders_used=any(item.placeholder for item in [timestamp, primary, category]),
    )


def _placeholder_mapping(warnings: list[str]) -> AssignmentDatasetMapping:
    timestamp = DatasetMappingSuggestion(column="TIMESTAMP_COLUMN", reason="placeholder until a profiled date/time column is available.", placeholder=True)
    numeric = DatasetMappingSuggestion(column="NUMERIC_COLUMN", reason="placeholder until a profiled numeric column is available.", placeholder=True)
    category = DatasetMappingSuggestion(column="CATEGORY_COLUMN", reason="placeholder until a profiled categorical column is available.", placeholder=True)
    return AssignmentDatasetMapping(
        timestamp_column=timestamp,
        primary_numeric_indicator=numeric,
        secondary_numeric_fields=[],
        category_grouping_column=category,
        classification_threshold_idea="Profile the dataset first, then choose a threshold from actual numeric values.",
        dashboard_filter_column=category,
        spark_aggregation_columns=["CATEGORY_COLUMN", "NUMERIC_COLUMN", "TIMESTAMP_COLUMN"],
        snowflake_table_names=["RAW_EVENTS", "CURATED_EVENTS", "AGG_EVENTS"],
        redis_key_patterns=["latest:{category}:{value}", "metric:{indicator}"],
        warnings=warnings,
        placeholders_used=True,
    )


def _suggestion(
    column: str | None,
    reason: str,
    placeholder: str,
    warnings: list[str],
    warning: str,
) -> DatasetMappingSuggestion:
    if column:
        return DatasetMappingSuggestion(column=column, reason=reason)
    warnings.append(warning)
    return DatasetMappingSuggestion(column=placeholder, reason=f"{placeholder} is a placeholder because profiling did not detect a suitable column.", placeholder=True)


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").upper()
    return cleaned or "EVENTS"
