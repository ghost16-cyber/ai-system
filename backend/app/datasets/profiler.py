from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.datasets.schemas import DatasetProfile, DatasetSuitability


SECRET_NAMES = {".env", "credentials.csv", "secrets.csv", "passwords.csv"}
SECRET_PARTS = ("secret", "credential", "password", "private_key", "api_key")
SUPPORTED_DATASET_EXTENSIONS = {".csv", ".txt", ".tsv"}
DELIMITER_CANDIDATES = (",", ";", "\t", "|")


def profile_csv_dataset(
    dataset_path: str | Path,
    *,
    sample_rows: int = 25,
    row_count_override: int | None = None,
) -> DatasetProfile:
    path = normalize_path_for_platform(dataset_path).path.expanduser().resolve()
    if _is_secret_path(path):
        raise ValueError("Refusing to profile secret-like dataset path.")
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    if not path.is_file():
        raise ValueError("Dataset path must point to a file. If you selected a folder, choose the actual .csv, .txt, or .tsv dataset file inside it.")
    if path.suffix.lower() not in SUPPORTED_DATASET_EXTENSIONS:
        raise ValueError("Unsupported dataset extension. Supported extensions: .csv, .txt, .tsv.")

    warnings: list[str] = []
    rows: list[dict[str, str]] = []
    row_count = 0
    delimiter = _detect_delimiter(path)
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter, restkey="__extra_fields__", restval=None)
        columns = [column.strip() for column in (reader.fieldnames or []) if column and column.strip()]
        if not columns:
            raise ValueError("Dataset has no header row.")
        missing = {column: 0 for column in columns}
        observed_values: dict[str, list[str]] = {column: [] for column in columns}
        for raw_row in reader:
            row_count += 1
            if raw_row.get("__extra_fields__"):
                warnings.append(f"Malformed row {row_count + 1}: extra fields were ignored.")
            if any(value is None for key, value in raw_row.items() if key != "__extra_fields__"):
                warnings.append(f"Malformed row {row_count + 1}: missing fields were treated as blank.")
            row = {column: str(raw_row.get(column, "") or "").strip() for column in columns}
            for column, value in row.items():
                if value == "":
                    missing[column] += 1
                elif len(observed_values[column]) < sample_rows:
                    observed_values[column].append(value)
            if len(rows) < sample_rows:
                rows.append(row)

    effective_count = row_count_override if row_count_override is not None else row_count
    date_columns = [column for column in columns if _looks_like_date_column(column, observed_values[column])]
    numeric_columns = [column for column in columns if _looks_numeric(observed_values[column])]
    categorical_columns = [
        column
        for column in columns
        if column not in numeric_columns and column not in date_columns and _looks_categorical(observed_values[column])
    ]
    if row_count > sample_rows:
        warnings.append(f"Profiled {sample_rows} sample row(s) for type detection; row count was streamed.")
    suitability = _suitability(
        row_count=effective_count,
        date_columns=date_columns,
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        columns=columns,
    )
    return DatasetProfile(
        dataset_path=str(path),
        detected_format=path.suffix.lower().lstrip("."),
        detected_delimiter=delimiter,
        row_count_estimate=effective_count,
        column_count=len(columns),
        columns=columns,
        detected_date_columns=date_columns,
        detected_numeric_columns=numeric_columns,
        detected_categorical_columns=categorical_columns,
        missing_value_summary=missing,
        sample_rows_limited=rows,
        warnings=warnings,
        suitability=suitability,
    )


def _detect_delimiter(path: Path) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        sample = handle.read(8192)
    lines = [line for line in sample.splitlines() if line.strip()][:10]
    if not lines:
        return ","
    scores: dict[str, int] = {}
    for delimiter in DELIMITER_CANDIDATES:
        counts = [len(next(csv.reader([line], delimiter=delimiter))) for line in lines]
        useful = [count for count in counts if count > 1]
        scores[delimiter] = (min(useful) * len(useful)) if useful else 0
    return max(
        enumerate(DELIMITER_CANDIDATES),
        key=lambda item: (scores[item[1]], -item[0]),
    )[1]


def _suitability(
    *,
    row_count: int,
    date_columns: list[str],
    numeric_columns: list[str],
    categorical_columns: list[str],
    columns: list[str],
) -> DatasetSuitability:
    reasons: list[str] = []
    a1 = row_count >= 20_000 and bool(date_columns) and len(numeric_columns) >= 2
    a2 = row_count >= 30_000 and bool(date_columns) and bool(categorical_columns) and bool(numeric_columns)
    a3 = row_count >= 15_000 and bool(numeric_columns)
    if not a1:
        reasons.append("Assignment 1 needs 20,000+ rows, a timestamp/date column, and multiple numeric fields.")
    if not a2:
        reasons.append("Assignment 2 needs 30,000+ rows plus date, categorical, and numeric columns.")
    if not a3:
        reasons.append("Assignment 3 needs 15,000+ rows and at least one numeric indicator for streaming/classification.")
    recommended = "none"
    if a2:
        recommended = "assignment_2"
    elif a1:
        recommended = "assignment_1"
    elif a3:
        recommended = "assignment_3"
    suggestions = {
        "date": date_columns[:3],
        "numeric": numeric_columns[:5],
        "categorical": categorical_columns[:5],
        "all": columns[:8],
    }
    return DatasetSuitability(
        assignment_1_suitable=a1,
        assignment_2_suitable=a2,
        assignment_3_suitable=a3,
        reasons=reasons,
        recommended_assignment_use=recommended,
        columns_to_use_suggestions=suggestions,
    )


def _is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    return name in SECRET_NAMES or any(part in name for part in SECRET_PARTS)


def _looks_like_date_column(column: str, values: list[str]) -> bool:
    lowered = column.lower()
    if any(term in lowered for term in ("date", "time", "timestamp")):
        return True
    tested = [value for value in values if value][:10]
    return bool(tested) and sum(1 for value in tested if _parse_date(value)) >= max(1, len(tested) // 2)


def _parse_date(value: str) -> bool:
    candidates = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d")
    for fmt in candidates:
        try:
            datetime.strptime(value[:19], fmt)
            return True
        except ValueError:
            continue
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}T", value))


def _looks_numeric(values: list[str]) -> bool:
    tested = [value for value in values if value][:20]
    if not tested:
        return False
    numeric = 0
    for value in tested:
        try:
            float(value)
            numeric += 1
        except ValueError:
            pass
    return numeric >= max(1, int(len(tested) * 0.8))


def _looks_categorical(values: list[str]) -> bool:
    tested = [value for value in values if value][:25]
    return bool(tested) and len(set(tested)) <= max(20, len(tested))
