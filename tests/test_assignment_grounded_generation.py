from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.app.assignments.code_blueprints import generate_code_blueprints
from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.grounded_generation import build_grounded_generation_plan
from backend.app.assignments.schemas import ParsedAssignmentDocument
from backend.app.rag.corpus_retrieval import CorpusSourceMetadata


BRIEF_TEXT = """
Assignment 2: PySpark + Snowflake + Streamlit
Task: Clean data with PySpark, load it into Snowflake, and build a Streamlit dashboard. 25 marks
Screenshot required: Snowflake worksheet and Streamlit dashboard.
Analysis question: Explain the data pipeline and dashboard design.
"""


def _brief():
    parsed = ParsedAssignmentDocument(
        document_id="grounded-test",
        title="Assignment 2",
        source_path="<inline>",
        extracted_text=BRIEF_TEXT,
        created_at=datetime.now(UTC),
    )
    return extract_assignment_brief(parsed)


def _source(
    chunk_id: str,
    path: str,
    preview: str,
    score: float = 0.9,
) -> CorpusSourceMetadata:
    return CorpusSourceMetadata(
        source_path=path,
        chunk_id=chunk_id,
        chunk_index=0,
        start_line=4,
        end_line=12,
        score=score,
        text_preview=preview,
    )


def _build(mode="mixed", sources=None):
    brief = _brief()
    return build_grounded_generation_plan(
        brief,
        assignment_number=2,
        workspace_path="assignment_workspaces/assignment_2",
        blueprint_set=generate_code_blueprints(2),
        corpus_sources=sources or [],
        evidence=build_evidence_checklist(brief),
        generation_mode=mode,
    )


def test_workspace_plan_creation_and_generated_documentation() -> None:
    result = _build()
    plan = result.workspace_generation_plan
    paths = {item.file_path for item in result.grounded_file_blueprints}

    assert plan.assignment_number == 2
    assert plan.workspace_path == "assignment_workspaces/assignment_2"
    assert plan.technologies
    assert "dashboard" in plan.directories
    assert plan.evidence_placeholders
    assert plan.report_placeholders
    assert plan.recommended_manual_configuration_steps
    assert {
        "snowflake_loader.py",
        "dashboard/app.py",
        "README.md",
        "architecture.md",
        "evidence_checklist.md",
        "report_outline.md",
        "runbook.md",
        ".env.example",
        "snowflake/schema.sql",
    }.issubset(paths)


def test_relevant_source_maps_to_file_and_provenance_is_structured() -> None:
    relevant = _source(
        "snowflake-1",
        "examples/snowflake_loader.py",
        "Snowflake warehouse connection and batch loading structure.",
    )
    result = _build(sources=[relevant])
    loader = next(
        item
        for item in result.grounded_file_blueprints
        if item.file_path == "snowflake_loader.py"
    )

    assert loader.generation_mode == "corpus_grounded"
    assert loader.source_ids == ["snowflake-1"]
    assert loader.grounding[0].source_path == "examples/snowflake_loader.py"
    assert loader.grounding[0].start_line == 4
    assert "vetted Astra template" in loader.grounding[0].influence


def test_unrelated_low_score_and_duplicate_sources_are_excluded() -> None:
    unrelated = _source("redis-1", "examples/redis_helper.py", "Redis cache operations.")
    low_score = _source(
        "snowflake-low",
        "examples/snowflake.py",
        "Snowflake loader.",
        score=0.1,
    )
    duplicate = _source(
        "snowflake-1",
        "examples/snowflake.py",
        "Snowflake loader structure.",
    )
    result = _build(sources=[unrelated, low_score, duplicate, duplicate])
    all_grounding = [
        item
        for blueprint in result.grounded_file_blueprints
        for item in blueprint.grounding
    ]

    assert all(item.chunk_id != "redis-1" for item in all_grounding)
    assert all(item.chunk_id != "snowflake-low" for item in all_grounding)
    assert all(
        len(blueprint.source_ids) == len(set(blueprint.source_ids))
        for blueprint in result.grounded_file_blueprints
    )
    assert result.corpus_grounding_summary.excluded_source_count >= 2


def test_generation_modes_and_no_source_fallback() -> None:
    source = _source(
        "snowflake-1",
        "examples/snowflake.py",
        "Snowflake table loading structure.",
    )
    template = _build(mode="template_only", sources=[source])
    mixed = _build(mode="mixed", sources=[source])
    grounded_without_sources = _build(mode="corpus_grounded")

    assert all(item.generation_mode == "template_only" for item in template.grounded_file_blueprints)
    assert any(item.generation_mode == "corpus_grounded" for item in mixed.grounded_file_blueprints)
    assert grounded_without_sources.generation_ready is True
    assert all(
        item.generation_mode == "template_only"
        for item in grounded_without_sources.grounded_file_blueprints
    )
    assert grounded_without_sources.workspace_generation_plan.unresolved_requirements
    assert grounded_without_sources.generation_warnings


def test_env_example_has_placeholders_and_documentation_makes_no_execution_claims() -> None:
    result = _build()
    by_path = {item.file_path: item.generated_content for item in result.grounded_file_blueprints}
    environment = by_path[".env.example"]

    assert "SNOWFLAKE_ACCOUNT=<set_manually>" in environment
    assert "SNOWFLAKE_PASSWORD=<set_manually>" in environment
    assert "password123" not in environment.lower()
    assert "No generated code or command has been executed" in by_path["README.md"]
    assert "All items are placeholders" in by_path["evidence_checklist.md"]
    assert "Add only measured, verified results" in by_path["report_outline.md"]
    assert "Astra did not execute" in by_path["runbook.md"]


def test_assignment_copilot_preserves_existing_fields_and_adds_generation(tmp_path: Path) -> None:
    from backend.app.assignments.copilot import run_assignment_copilot

    result = run_assignment_copilot(
        text=BRIEF_TEXT,
        selected_assignment=2,
        workspace_path=tmp_path,
        use_corpus=False,
    )

    assert result.action_plan.checklist
    assert result.evidence_checklist.items
    assert result.report_draft.sections
    assert result.code_blueprints
    assert result.workspace_generation_plan
    assert result.grounded_file_blueprints
    assert result.generation_ready is True
    assert result.files_written is False
    assert result.tools_executed is False


def test_missing_and_malformed_vector_store_use_template_fallback(tmp_path: Path) -> None:
    from backend.app.assignments.copilot import run_assignment_copilot

    missing = run_assignment_copilot(
        text=BRIEF_TEXT,
        selected_assignment=2,
        workspace_path=tmp_path,
        corpus_workspace_root=tmp_path,
    )
    vector_root = tmp_path / "data" / "rag" / "corpus_vectors"
    vector_root.mkdir(parents=True)
    (vector_root / "manifest.json").write_text("{bad json", encoding="utf-8")
    malformed = run_assignment_copilot(
        text=BRIEF_TEXT,
        selected_assignment=2,
        workspace_path=tmp_path,
        corpus_workspace_root=tmp_path,
    )

    assert missing.corpus_retrieval_skip_reason == "vector_store_unavailable"
    assert malformed.corpus_retrieval_skip_reason == "vector_store_unavailable"
    assert missing.generation_ready is True
    assert malformed.generation_ready is True
    assert all(item.generation_mode == "template_only" for item in malformed.grounded_file_blueprints)
