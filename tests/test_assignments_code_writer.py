from __future__ import annotations

from pathlib import Path

from backend.app.assignments.code_blueprints import generate_code_blueprints
from backend.app.assignments.code_writer import write_code_blueprints
from backend.app.assignments.schemas import AssignmentCodeBlueprintSet
from backend.app.datasets import profile_csv_dataset


def _profile(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text("timestamp,value,humidity,site\n2026-01-01,10,44,A\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_code_writer_writes_blueprint_files_inside_workspace(tmp_path: Path):
    result = write_code_blueprints(tmp_path / "workspace", generate_code_blueprints(1, dataset_profile=_profile(tmp_path)))

    assert "producer.py" in result.created_files
    assert (tmp_path / "workspace" / "producer.py").exists()
    assert result.commands_executed is False


def test_code_writer_refuses_path_traversal(tmp_path: Path):
    blueprints = generate_code_blueprints(1, dataset_profile=_profile(tmp_path))
    unsafe = blueprints.model_copy(update={"blueprints": [blueprints.blueprints[0].model_copy(update={"file_path": "../outside.py"})]})

    result = write_code_blueprints(tmp_path, unsafe)

    assert "../outside.py" in result.refused_files
    assert not (tmp_path.parent / "outside.py").exists()


def test_code_writer_refuses_absolute_outside_path(tmp_path: Path):
    blueprints = generate_code_blueprints(1, dataset_profile=_profile(tmp_path))
    unsafe = blueprints.model_copy(update={"blueprints": [blueprints.blueprints[0].model_copy(update={"file_path": str(tmp_path.parent / "outside.py")})]})

    result = write_code_blueprints(tmp_path, unsafe)

    assert result.refused_files


def test_code_writer_skips_existing_files_by_default(tmp_path: Path):
    (tmp_path / "producer.py").write_text("keep\n", encoding="utf-8")

    result = write_code_blueprints(tmp_path, generate_code_blueprints(1, dataset_profile=_profile(tmp_path)))

    assert "producer.py" in result.skipped_files
    assert (tmp_path / "producer.py").read_text(encoding="utf-8") == "keep\n"


def test_code_writer_overwrites_only_when_requested(tmp_path: Path):
    (tmp_path / "producer.py").write_text("keep\n", encoding="utf-8")

    result = write_code_blueprints(tmp_path, generate_code_blueprints(1, dataset_profile=_profile(tmp_path)), overwrite=True)

    assert "producer.py" in result.created_files
    assert "KafkaProducer" in (tmp_path / "producer.py").read_text(encoding="utf-8")


def test_code_writer_does_not_write_real_credentials(tmp_path: Path):
    blueprints = AssignmentCodeBlueprintSet(
        assignment_number=1,
        blueprints=[
            generate_code_blueprints(1).blueprints[0].model_copy(
                update={"file_path": "bad.py", "generated_content": 'PASSWORD = "actual-secret-value"\n'}
            )
        ],
    )

    result = write_code_blueprints(tmp_path, blueprints)

    assert "bad.py" in result.refused_files
    assert not (tmp_path / "bad.py").exists()
    assert result.credentials_written is False


def test_code_writer_output_is_deterministic(tmp_path: Path):
    first = write_code_blueprints(tmp_path / "one", generate_code_blueprints(3)).model_dump(mode="json")
    second = write_code_blueprints(tmp_path / "two", generate_code_blueprints(3)).model_dump(mode="json")

    assert [Path(path).name for path in first["created_files"]] == [Path(path).name for path in second["created_files"]]
    assert first["next_manual_steps"] == second["next_manual_steps"]
