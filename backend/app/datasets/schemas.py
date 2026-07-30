from __future__ import annotations

from pydantic import BaseModel, Field


class DatasetSuitability(BaseModel):
    assignment_1_suitable: bool
    assignment_2_suitable: bool
    assignment_3_suitable: bool
    reasons: list[str] = Field(default_factory=list)
    recommended_assignment_use: str
    columns_to_use_suggestions: dict[str, list[str]] = Field(default_factory=dict)


class DatasetProfile(BaseModel):
    dataset_path: str
    detected_format: str
    detected_delimiter: str = ","
    row_count_estimate: int
    column_count: int
    columns: list[str] = Field(default_factory=list)
    detected_date_columns: list[str] = Field(default_factory=list)
    detected_numeric_columns: list[str] = Field(default_factory=list)
    detected_categorical_columns: list[str] = Field(default_factory=list)
    missing_value_summary: dict[str, int] = Field(default_factory=dict)
    sample_rows_limited: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suitability: DatasetSuitability
