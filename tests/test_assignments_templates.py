from __future__ import annotations

from pathlib import Path

from backend.app.assignments import (
    AssignmentTemplatePlan,
    generate_assignment_template_plan,
    write_assignment_template_plan,
)


def _paths(plan):
    return {item.file_path for item in plan.files}


def test_each_assignment_type_generates_expected_files():
    one = generate_assignment_template_plan(1)
    two = generate_assignment_template_plan(2)
    three = generate_assignment_template_plan(3)

    assert {"docker-compose.yml", "producer.py", "consumer_to_influx.py", "requirements.txt", "README.md", "report_outline.md"} <= _paths(one)
    assert {"spark_processing.py", "snowflake_loader.py", "dashboard/app.py", "config/example.env", "requirements.txt", "README.md", "report_outline.md"} <= _paths(two)
    assert {"replay_producer.py", "structured_streaming_job.py", "redis_helper.py", "dashboard/app.py", "docker-compose.yml", "requirements.txt", "README.md", "report_outline.md"} <= _paths(three)


def test_generated_templates_do_not_include_real_credentials():
    plan = generate_assignment_template_plan(2)
    combined = "\n".join(file.content_preview.lower() for file in plan.files)

    assert "real-secret" not in combined
    assert "actual_password" not in combined
    assert "replace_with_account" in combined
    assert "externalbrowser" in combined


def test_template_write_refuses_unsafe_paths(tmp_path: Path):
    plan = AssignmentTemplatePlan(
        assignment_number=1,
        assignment_name="unsafe",
        files=[
            generate_assignment_template_plan(1).files[0].model_copy(update={"file_path": "../outside.txt"}),
            generate_assignment_template_plan(1).files[1],
        ],
    )

    result = write_assignment_template_plan(tmp_path, plan)

    assert "../outside.txt" in result.refused_files
    assert "producer.py" in result.created_files
    assert not (tmp_path.parent / "outside.txt").exists()


def test_template_write_does_not_overwrite_by_default(tmp_path: Path):
    (tmp_path / "README.md").write_text("keep me\n", encoding="utf-8")
    plan = generate_assignment_template_plan(1)

    result = write_assignment_template_plan(tmp_path, plan)

    assert "README.md" in result.skipped_files
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "keep me\n"


def test_generated_report_outline_includes_screenshots_and_analysis_questions():
    plan = generate_assignment_template_plan(3)
    outline = next(file for file in plan.files if file.file_path == "report_outline.md")

    assert "Screenshot checklist" in outline.content_preview
    assert "Analysis questions" in outline.content_preview
    assert "Marking checklist" in outline.content_preview
