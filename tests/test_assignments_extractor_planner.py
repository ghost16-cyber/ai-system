from __future__ import annotations

from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.final_readiness import build_final_readiness_report
from backend.app.assignments.planner import build_assignment_plan


MINI_BRIEF = """
Big Data Practical Assignment Portfolio

Assignment 1: Apache Kafka + InfluxDB + Grafana
Task: Build a Kafka producer and stream sensor data into InfluxDB. 20 marks
Create a Grafana dashboard for the time-series data. 10 marks
Screenshot required: Kafka topic output and Grafana dashboard.
Analysis question: Explain why Kafka is suitable for streaming ingestion?
Dataset requirement: use the provided sensor CSV dataset.
Bonus: Add alerting in Grafana.

Assignment 2: Apache Spark/PySpark + Snowflake + Streamlit
Task: Implement a PySpark ETL pipeline and load results into Snowflake. 25 marks
Build a Streamlit dashboard for the cleaned dataset. 15 marks
Screenshot required: Snowflake table and Streamlit dashboard.
Report requirement: discuss data quality and performance.
Analysis question: Compare Spark batch processing with streaming.
"""


BIG_DATA_CLEANUP_BRIEF = """
Big Data 07 Portfolio
Each assignment is completed in groups of two. Register dataset in class.
Submit one report per assignment. Keep report concise.
Suggested dataset sources include Kaggle and government portals.

Assignment 1: Real-time pipeline with Kafka, InfluxDB and Grafana
Task 1 — Kafka Producer [6 marks]
Implement a producer that sends records to a Kafka topic.
SCREENSHOT REQUIRED: Docker containers running and Producer terminal showing records being sent.
Task 2 — Consumer with Enrichment [6 marks]
Enrich records and send them onward.
Screenshot required: Consumer running with enriched output.
Task 3 — Sink to InfluxDB [5 marks]
Write enriched records into InfluxDB.
Screenshot required: InfluxDB Data Explorer.
Task 4 — Grafana Dashboard [9 marks]
Build panels for the streaming data.
Screenshot required: Grafana dashboard.
Task 5 — Analysis [4 marks]
Analysis question: Explain how Kafka decouples producers and consumers?
Marking criteria:
- Producer correctness [6 marks]
- Dashboard quality [9 marks]
Bonus: Add alerting screenshot in Grafana.
Every screenshot must be clear.
[Insert screenshot here]

Assignment 2: PySpark and Snowflake analytics
Task 1 — Load, Clean & Profile [5 marks]
Load the CSV, clean it, and show schema and data quality.
Screenshot required: PySpark terminal schema and data quality summary.
Task 2 — Aggregations & Spark SQL [6 marks]
Run aggregation query results.
Screenshot required: aggregation query results.
Task 3 — Window Functions [5 marks]
Compute window function outputs.
Screenshot required: window function outputs.
Task 4 — Load Data into Snowflake [6 marks]
Load tables into Snowflake.
Screenshot required: Snowflake object browser and Snowflake worksheet query results.
Task 5 — Build an Interactive Dashboard [8 marks]
Build a Streamlit dashboard with KPIs/charts/table.
Screenshot required: Streamlit dashboard with KPIs/charts/table and Streamlit dashboard after filter.
Analysis questions:
Discuss data quality decisions.
Compare Spark SQL with DataFrame transformations.

Assignment 3: Structured Streaming and Redis
Task 1 — Kafka Replay Producer [4 marks]
Replay records into Kafka.
Task 2 — PySpark Structured Streaming Job [10 marks]
Write streaming aggregates to Redis.
Screenshot required: Redis CLI output and streaming query progress logs.
Task 3 — Watermarking & Late Data [6 marks]
Demonstrate watermark/query plan/logs.
Screenshot required: watermark/query plan/logs.
Task 4 — Streamlit Live Dashboard [8 marks]
Show a live dashboard.
Screenshot required: Streamlit dashboard after filter.
"""


def test_assignment_instruction_extractor_finds_sections_and_requirements():
    brief = extract_assignment_brief(MINI_BRIEF)

    assert brief.title == "Big Data Practical Assignment Portfolio"
    assert len(brief.sections) == 2
    assert "Kafka" in brief.technologies
    assert "InfluxDB" in brief.technologies
    assert "PySpark" in brief.technologies
    assert "Snowflake" in brief.technologies
    assert any(task.marks == 20 for task in brief.sections[0].tasks)
    assert len(brief.screenshot_requirements) == 2
    assert len(brief.analysis_questions) == 2
    assert brief.bonus_requirements[0].optional is True
    assert brief.dataset_requirements
    assert brief.report_requirements


def test_assignment_planner_converts_tasks_to_checklist_items():
    brief = extract_assignment_brief(MINI_BRIEF)
    plan = build_assignment_plan(brief)

    task_titles = [item.title for item in plan.checklist]
    assert any("Kafka producer" in title for title in task_titles)
    assert any("PySpark ETL" in title for title in task_titles)
    assert all(item.status == "todo" for item in plan.checklist)


def test_assignment_planner_screenshot_requirements_become_evidence_items():
    plan = build_assignment_plan(extract_assignment_brief(MINI_BRIEF))

    screenshot_items = [item for item in plan.checklist if item.screenshot_needed]
    assert screenshot_items
    assert any("Grafana" in " ".join(item.evidence_needed) for item in screenshot_items)


def test_assignment_planner_analysis_questions_become_report_items():
    plan = build_assignment_plan(extract_assignment_brief(MINI_BRIEF))

    report_items = [item for item in plan.checklist if item.report_section_needed]
    assert any("Kafka is suitable" in " ".join(item.evidence_needed) for item in report_items)
    assert "Report writing" in plan.groups


def test_assignment_planner_bonus_items_are_optional():
    plan = build_assignment_plan(extract_assignment_brief(MINI_BRIEF))

    bonus_items = [item for item in plan.checklist if item.optional]
    assert bonus_items
    assert any("Bonus" in item.title or "bonus" in item.title.lower() for item in bonus_items)


def test_assignment_planner_is_deterministic():
    first = build_assignment_plan(extract_assignment_brief(MINI_BRIEF))
    second = build_assignment_plan(extract_assignment_brief(MINI_BRIEF))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.summary_groups == [
        "Setup",
        "Data preparation",
        "Pipeline/code implementation",
        "Dashboard",
        "Evidence/screenshots",
        "Report writing",
        "Final marking check",
    ]


def test_phase79_general_instructions_are_not_normal_tasks():
    brief = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF)
    task_text = " ".join(task.title for section in brief.sections for task in section.tasks).lower()

    assert "groups of two" not in task_text
    assert "submit one report" not in task_text
    assert any("groups of two" in item.lower() for item in brief.global_instructions)
    assert any("submit one report" in item.lower() for item in brief.report_guidance)


def test_phase79_task_headings_with_marks_are_extracted_as_tasks():
    brief = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF)
    titles = [task.title for section in brief.sections for task in section.tasks]

    assert "Kafka Producer" in titles
    assert "Consumer with Enrichment" in titles
    assert "Load, Clean & Profile" in titles
    assert "PySpark Structured Streaming Job" in titles
    assert any(task.marks == 6 for section in brief.sections for task in section.tasks)


def test_phase79_screenshot_requirements_attach_to_nearby_task():
    brief = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF)
    producer_shot = next(item for item in brief.sections[0].screenshot_requirements if "Producer terminal" in item.description)

    assert producer_shot.task_name == "Kafka Producer"


def test_phase79_marking_criteria_are_extracted_separately():
    brief = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF)

    assert any("Producer correctness" in item.description for item in brief.sections[0].marking_criteria)
    assert any("Dashboard quality" in item.description for item in brief.sections[0].marking_criteria)


def test_phase79_analysis_questions_are_extracted_separately():
    brief = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF)
    questions = [item.question for item in brief.analysis_questions]

    assert any("Kafka decouples" in item for item in questions)
    assert any("data quality decisions" in item for item in questions)


def test_phase79_bonus_items_are_optional():
    brief = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF)

    assert brief.bonus_requirements
    assert all(item.optional for item in brief.bonus_requirements)


def test_phase79_evidence_count_for_big_data_fixture_is_reasonable():
    checklist = build_evidence_checklist(extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF))

    assert 25 <= len(checklist.items) <= 45
    assert not any("submit one report" in item.title.lower() for item in checklist.items)
    assert not any("every screenshot must be clear" in item.title.lower() for item in checklist.items)


def test_phase79_final_readiness_does_not_include_generic_instruction_blockers():
    report = build_final_readiness_report(extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF), assignment_number=1)
    blockers = " ".join(report.missing_blockers).lower()

    assert "groups of two" not in blockers
    assert "submit one report" not in blockers
    assert "every screenshot must be clear" not in blockers


def test_phase79_extraction_output_is_deterministic():
    first = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF).model_dump(mode="json")
    second = extract_assignment_brief(BIG_DATA_CLEANUP_BRIEF).model_dump(mode="json")

    assert first == second
