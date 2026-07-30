from __future__ import annotations

import re

from backend.app.assignments.schemas import (
    AssignmentDatasetMapping,
    DatasetMappingSuggestion,
    DatasetSemanticMapping,
    DerivedColumnPlan,
    MappingEvidence,
    SemanticField,
    SourceColumn,
)
from backend.app.datasets.schemas import DatasetProfile


class DatasetSemanticMappingError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_derived_dataset_mapping") -> None:
        super().__init__(message)
        self.code = code


def map_dataset_columns(profile: DatasetProfile | None) -> AssignmentDatasetMapping:
    if profile is None:
        return _unresolved_mapping("Dataset has not been profiled.")

    known = list(dict.fromkeys(profile.columns))
    if not known:
        return _unresolved_mapping("The profiled dataset does not contain columns.", dataset_path=profile.dataset_path)
    date_columns = [item for item in profile.detected_date_columns if item in known]
    numeric_columns = [item for item in profile.detected_numeric_columns if item in known]
    categorical_columns = [item for item in profile.detected_categorical_columns if item in known]
    lower = {item.lower(): item for item in known}
    date_only = next((lower[name] for name in ("date", "event_date", "day") if name in lower), None)
    time_only = next((lower[name] for name in ("time", "event_time", "timestamp_time") if name in lower), None)

    evidence = tuple(
        MappingEvidence(
            evidence_type="dataset_schema", source_identifier=profile.dataset_path,
            source_columns=(column,), observation=f"Column {column!r} exists in the profiled schema.",
        )
        for column in known
    )
    source_columns = tuple(
        SourceColumn(
            name=column,
            inferred_type=("numeric" if column in numeric_columns else "date_or_time" if column in date_columns or column in {date_only, time_only} else "categorical" if column in categorical_columns else "text"),
            roles=tuple(role for role, values in (("measure", numeric_columns), ("dimension", categorical_columns), ("time", date_columns)) if column in values),
        )
        for column in known
    )
    derived: list[DerivedColumnPlan] = []
    warnings: list[str] = []
    unresolved: list[str] = []

    if date_only and time_only and date_only != time_only:
        timestamp_name = "event_timestamp"
        timestamp_sources = (date_only, time_only)
        derived.append(_derived(
            timestamp_name, "combine_datetime", timestamp_sources,
            "trim both values, join with one space, and parse deterministically as a timestamp",
            "timestamp", "The dataset stores calendar date and clock time separately.", profile.dataset_path,
        ))
    elif date_columns:
        timestamp_name = date_columns[0]
        timestamp_sources = (timestamp_name,)
    elif date_only:
        timestamp_name = date_only
        timestamp_sources = (date_only,)
    else:
        timestamp_name = ""
        timestamp_sources = ()
        unresolved.append("A timestamp/time dimension could not be identified or derived from the dataset schema.")

    time_field = SemanticField(
        name=timestamp_name, field_type="derived" if any(item.name == timestamp_name for item in derived) else "source",
        semantic_role="time", source_columns=timestamp_sources,
        rationale="Use the validated event time for ordering, windows, and dashboard filters.",
        provenance=_evidence_for(evidence, timestamp_sources),
    ) if timestamp_name else None

    measures = tuple(
        SemanticField(
            name=column, field_type="source", semantic_role="measure", source_columns=(column,),
            rationale="Profiled numeric column suitable for deterministic aggregation.",
            provenance=_evidence_for(evidence, (column,)),
        )
        for column in numeric_columns[:5]
    )
    if not measures:
        unresolved.append("A numeric measure could not be identified from the dataset schema.")

    dimensions: list[SemanticField] = [
        SemanticField(
            name=column, field_type="source", semantic_role="dimension", source_columns=(column,),
            rationale="Profiled categorical column suitable for grouping and filters.",
            provenance=_evidence_for(evidence, (column,)),
        ) for column in categorical_columns[:5]
    ]
    if not dimensions and timestamp_name:
        for name, part, rationale in (
            ("event_hour", "hour", "Hour provides a deterministic dashboard and aggregation dimension."),
            ("event_weekday", "weekday", "Weekday provides a deterministic recurring-time dimension."),
        ):
            derived.append(_derived(
                name, "date_part", timestamp_sources, f"extract {part} from {timestamp_name}",
                "integer" if part == "hour" else "string", rationale, profile.dataset_path,
            ))
            dimensions.append(SemanticField(
                name=name, field_type="derived", semantic_role="dimension", source_columns=timestamp_sources,
                rationale=rationale, provenance=_evidence_for(evidence, timestamp_sources),
            ))
        warnings.append("No categorical source column was detected; deterministic time dimensions were planned.")
    elif not dimensions and measures:
        source = measures[0].name
        name = f"{_identifier(source).lower()}_band"
        derived.append(_derived(
            name, "numeric_bin", (source,),
            f"bucket {source} at the deterministic zero boundary into positive and non-positive bands",
            "string", "A deterministic numeric band supplies a grouping dimension.", profile.dataset_path,
        ))
        dimensions.append(SemanticField(
            name=name, field_type="derived", semantic_role="dimension", source_columns=(source,),
            rationale="Use a declared numeric band when no categorical source field exists.",
            provenance=_evidence_for(evidence, (source,)),
        ))
        warnings.append("No categorical source column was detected; a numeric-band derivation was planned.")
    if not dimensions:
        unresolved.append("No categorical dimension exists and no safe deterministic dimension can be derived.")

    semantic = DatasetSemanticMapping(
        source_columns=source_columns, derived_columns=tuple(derived), time_dimension=time_field,
        numeric_measures=measures, categorical_dimensions=tuple(dimensions),
        quality_warnings=tuple(warnings), unresolved_requirements=tuple(unresolved), provenance=evidence,
    )
    primary_name = measures[0].name if measures else ""
    category_name = dimensions[0].name if dimensions else ""
    timestamp = _suggestion(timestamp_name, "Validated source or deterministic derived event time.")
    primary = _suggestion(primary_name, "Validated numeric measure for aggregations and KPIs.")
    category = _suggestion(category_name, "Validated source or deterministic derived grouping dimension.")
    table_base = _identifier(known[0] if known else "events")
    return AssignmentDatasetMapping(
        dataset_path=profile.dataset_path,
        timestamp_column=timestamp,
        primary_numeric_indicator=primary,
        secondary_numeric_fields=[_suggestion(item.name, item.rationale) for item in measures[1:]],
        category_grouping_column=category,
        classification_threshold_idea=(
            f"Use the declared deterministic zero boundary for `{primary_name}`, or approve a domain threshold in a later revision."
            if primary_name else "A numeric threshold is unresolved until a measure is available."
        ),
        dashboard_filter_column=category,
        spark_aggregation_columns=[item for item in (category_name, primary_name, timestamp_name) if item],
        snowflake_table_names=[f"RAW_{table_base}", f"CURATED_{table_base}", f"AGG_{table_base}"],
        redis_key_patterns=[f"latest:{_identifier(category_name or 'dimension').lower()}:{{value}}", f"metric:{_identifier(primary_name or 'measure').lower()}"],
        warnings=[*warnings, *unresolved],
        placeholders_used=bool(unresolved), semantic_mapping=semantic,
        unresolved_requirements=unresolved,
    )


def require_resolved_semantic_mapping(profile: DatasetProfile | None) -> AssignmentDatasetMapping:
    mapping = map_dataset_columns(profile)
    if mapping.unresolved_requirements:
        raise DatasetSemanticMappingError(
            "; ".join(mapping.unresolved_requirements), code="unresolved_semantic_dimension"
        )
    return mapping


def _unresolved_mapping(reason: str, *, dataset_path: str | None = None) -> AssignmentDatasetMapping:
    unresolved = [reason]
    empty = DatasetMappingSuggestion(column="", reason=reason, placeholder=True)
    semantic = DatasetSemanticMapping(
        source_columns=(), derived_columns=(), time_dimension=None, numeric_measures=(),
        categorical_dimensions=(), quality_warnings=(), unresolved_requirements=(reason,), provenance=(),
    )
    return AssignmentDatasetMapping(
        dataset_path=dataset_path, timestamp_column=empty,
        primary_numeric_indicator=empty, secondary_numeric_fields=[],
        category_grouping_column=empty, classification_threshold_idea=reason,
        dashboard_filter_column=empty, spark_aggregation_columns=[],
        snowflake_table_names=["RAW_EVENTS", "CURATED_EVENTS", "AGG_EVENTS"],
        redis_key_patterns=[], warnings=unresolved, placeholders_used=True,
        semantic_mapping=semantic, unresolved_requirements=unresolved,
    )


def _derived(
    name: str, expression_type: str, source_columns: tuple[str, ...], operation: str,
    output_type: str, rationale: str, source_identifier: str,
) -> DerivedColumnPlan:
    proof = MappingEvidence(
        evidence_type="derived_rule", source_identifier=source_identifier,
        source_columns=source_columns, observation=operation,
    )
    return DerivedColumnPlan(
        name=name, expression_type=expression_type, source_columns=source_columns,
        deterministic_operation=operation, output_type=output_type,
        rationale=rationale, provenance=(proof,),
    )


def _evidence_for(evidence: tuple[MappingEvidence, ...], columns: tuple[str, ...]) -> tuple[MappingEvidence, ...]:
    selected = tuple(item for item in evidence if set(item.source_columns) & set(columns))
    return selected or tuple(item for item in evidence if not columns)[:1]


def _suggestion(column: str, reason: str) -> DatasetMappingSuggestion:
    return DatasetMappingSuggestion(column=column, reason=reason, placeholder=not bool(column))


def _identifier(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").upper()
    return cleaned or "EVENTS"
