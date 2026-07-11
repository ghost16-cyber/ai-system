from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.assignments.grounded_generation import write_grounded_workspace
from backend.app.assignments.schemas import (
    CorpusGroundingSummary,
    GroundedFileBlueprint,
)
from backend.app.main import create_app


BRIEF = """
Assignment 2: Snowflake + Streamlit
Task: Load prepared data to Snowflake and build a Streamlit dashboard.
Screenshot required: Snowflake worksheet and dashboard.
"""


def _blueprint(path: str = "safe/starter.py", content: str = "print('review before running')\n"):
    return GroundedFileBlueprint(
        file_path=path,
        purpose="Safe starter",
        assignment_number=2,
        technology_area="Python",
        generation_mode="template_only",
        generated_content=content,
    )


def _summary() -> CorpusGroundingSummary:
    return CorpusGroundingSummary(template_file_count=1)


def test_writer_creates_files_atomically_without_execution(tmp_path: Path) -> None:
    result = write_grounded_workspace(
        tmp_path / "workspace",
        [_blueprint()],
        grounding_summary=_summary(),
    )

    assert result.created_files == ["safe/starter.py"]
    assert (tmp_path / "workspace" / "safe" / "starter.py").read_text(encoding="utf-8")
    assert result.commands_executed is False
    assert result.generated_code_executed is False


def test_writer_refuses_traversal_and_writes_nothing_outside_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = write_grounded_workspace(
        workspace,
        [_blueprint("../outside.py"), _blueprint("..\\windows-outside.py")],
        grounding_summary=_summary(),
    )

    assert result.refused_files == ["../outside.py", "..\\windows-outside.py"]
    assert not (tmp_path / "outside.py").exists()
    assert not (tmp_path / "windows-outside.py").exists()


def test_writer_reports_conflict_and_supports_explicit_overwrite(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first = write_grounded_workspace(
        workspace,
        [_blueprint(content="first\n")],
        grounding_summary=_summary(),
    )
    conflict = write_grounded_workspace(
        workspace,
        [_blueprint(content="second\n")],
        grounding_summary=_summary(),
    )
    overwritten = write_grounded_workspace(
        workspace,
        [_blueprint(content="second\n")],
        grounding_summary=_summary(),
        overwrite=True,
    )

    assert first.created_files == ["safe/starter.py"]
    assert conflict.conflicts == ["safe/starter.py"]
    assert conflict.skipped_files == ["safe/starter.py"]
    assert overwritten.created_files == ["safe/starter.py"]
    assert (workspace / "safe" / "starter.py").read_text(encoding="utf-8") == "second\n"


def test_writer_refuses_real_credentials(tmp_path: Path) -> None:
    result = write_grounded_workspace(
        tmp_path / "workspace",
        [_blueprint("config.py", 'PASSWORD="real-secret-value"\n')],
        grounding_summary=_summary(),
    )

    assert result.refused_files == ["config.py"]
    assert not (tmp_path / "workspace" / "config.py").exists()


def test_writer_does_not_follow_symlink_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    result = write_grounded_workspace(
        workspace,
        [_blueprint("linked/escape.py")],
        grounding_summary=_summary(),
    )

    assert result.refused_files == ["linked/escape.py"]
    assert not (outside / "escape.py").exists()


def _copilot_result(client: TestClient) -> dict:
    response = client.post(
        "/assignments/copilot/run",
        json={
            "text": BRIEF,
            "selected_assignment": 2,
            "workspace_path": ".",
            "use_corpus": False,
            "generation_mode": "mixed",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_workspace_generation_api_success_and_provenance(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        copilot = _copilot_result(client)
        response = client.post(
            "/assignments/workspace/generate",
            json={
                "assignment_number": 2,
                "workspace_path": "assignment_workspaces/assignment_2",
                "generation_mode": "mixed",
                "overwrite": False,
                "copilot_result": copilot,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "README.md" in body["created_files"]
    assert "dashboard/app.py" in body["created_files"]
    assert "snowflake_loader.py" in body["created_files"]
    assert body["grounding_summary"]["template_file_count"] >= 1
    assert body["commands_executed"] is False
    assert body["generated_code_executed"] is False


def test_workspace_generation_api_rejects_outside_and_traversal_paths(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        copilot = _copilot_result(client)
        outside = client.post(
            "/assignments/workspace/generate",
            json={
                "assignment_number": 2,
                "workspace_path": str(tmp_path.parent / "outside"),
                "copilot_result": copilot,
            },
        )
        traversal = client.post(
            "/assignments/workspace/generate",
            json={
                "assignment_number": 2,
                "workspace_path": "../outside",
                "copilot_result": copilot,
            },
        )

    assert outside.status_code == 400
    assert traversal.status_code == 400
    assert not (tmp_path.parent / "outside").exists()


def test_workspace_generation_api_controlled_failure_for_missing_blueprints(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/workspace/generate",
            json={
                "assignment_number": 2,
                "workspace_path": "assignment_workspaces/assignment_2",
                "copilot_result": {},
            },
        )

    assert response.status_code == 400
    assert "No grounded file blueprints" in response.json()["detail"]
