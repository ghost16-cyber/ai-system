from __future__ import annotations

from backend.app.assignments.schemas import AssignmentAnalysisPlan, AssignmentAnalysisQuestion
from backend.app.datasets.schemas import DatasetProfile


def generate_analysis_plan(
    assignment_number: int,
    *,
    dataset_profile: DatasetProfile | None = None,
) -> AssignmentAnalysisPlan:
    columns = _columns(dataset_profile)
    if assignment_number == 1:
        questions = [
            _q(1, 1, "How does the main numeric metric change over time?", "Grafana", f"Plot avg({columns['numeric']}) grouped by {columns['date']}.", [columns["date"], "avg_value"]),
            _q(1, 2, "Which category has the highest event volume?", "Grafana", f"Count records by {columns['category']}.", [columns["category"], "event_count"]),
            _q(1, 3, "What are the summary statistics for the streamed metric?", "Grafana", f"Calculate min, max, and mean for {columns['numeric']}.", ["min_value", "max_value", "avg_value"]),
            _q(1, 4, "Which stream events cross the chosen anomaly threshold?", "Grafana", f"Flag records where {columns['numeric']} exceeds [threshold].", [columns["date"], columns["numeric"], "threshold_flag"]),
        ]
    elif assignment_number == 2:
        questions = [
            _q(2, 1, "What is the average metric by category?", "DataFrame API", f"groupBy('{columns['category']}').agg(avg('{columns['numeric']}'))", [columns["category"], "avg_value"]),
            _q(2, 2, "How many records arrive per date period?", "DataFrame API", f"groupBy(to_date('{columns['date']}')).count()", ["event_date", "row_count"]),
            _q(2, 3, "Which categories have the highest total metric?", "Spark SQL", f"SELECT {columns['category']}, SUM({columns['numeric']}) FROM table GROUP BY {columns['category']}", [columns["category"], "total_value"]),
            _q(2, 4, "How does the metric vary by category and date?", "Spark SQL", f"SELECT date_trunc('day', {columns['date']}), {columns['category']}, AVG({columns['numeric']}) FROM table GROUP BY 1, 2", ["event_day", columns["category"], "avg_value"]),
        ]
    elif assignment_number == 3:
        questions = [
            _q(3, 1, "What is the rolling metric average in each tumbling window?", "Structured Streaming", f"Window {columns['date']} and average {columns['numeric']}.", ["window_start", "window_end", "avg_value"]),
            _q(3, 2, "How should records be classified into severity or status groups?", "Streaming classification", f"Create a severity indicator from {columns['classification']} or a threshold on {columns['numeric']}.", ["category", "severity", "event_count"]),
            _q(3, 3, "How does watermarking handle late data in this pipeline?", "Report prompt", "Explain watermark duration, late records, and dropped/updated windows.", ["watermark_delay", "late_record_note"]),
            _q(3, 4, "What changes between batch results and streaming results?", "Report prompt", "Compare latency, completeness, and update behavior without inventing results.", ["comparison_area", "batch_note", "stream_note"]),
        ]
    else:
        raise ValueError("Assignment number must be 1, 2, or 3.")
    return AssignmentAnalysisPlan(
        assignment_number=assignment_number,
        questions=questions,
        warnings=[] if dataset_profile else ["Dataset profile missing; placeholders are used for columns."],
    )


def _q(assignment: int, index: int, question: str, method: str, logic: str, outputs: list[str]) -> AssignmentAnalysisQuestion:
    return AssignmentAnalysisQuestion(
        question_id=f"a{assignment}-analysis-{index}",
        assignment_number=assignment,
        question=question,
        method=method,
        suggested_logic=logic,
        expected_output_columns=outputs,
        report_prompt="Write what the output shows after you run it; do not invent values.",
    )


def _columns(profile: DatasetProfile | None) -> dict[str, str]:
    date = profile.detected_date_columns[0] if profile and profile.detected_date_columns else "TIMESTAMP_COLUMN"
    numeric = profile.detected_numeric_columns[0] if profile and profile.detected_numeric_columns else "NUMERIC_COLUMN"
    category = profile.detected_categorical_columns[0] if profile and profile.detected_categorical_columns else "CATEGORY_COLUMN"
    classification = "CLASSIFICATION_COLUMN"
    if profile and profile.detected_numeric_columns:
        classification = next((col for col in profile.detected_numeric_columns if col.lower() in {"label", "target", "severity", "indicator"}), profile.detected_numeric_columns[0])
    return {"date": date, "numeric": numeric, "category": category, "classification": classification}
