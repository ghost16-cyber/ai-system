from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.folders.scanner import (
    FolderScanConfigError,
    FolderScanLimits,
    build_inventory,
    is_budget_exempt_dataset_content,
    is_ignored_directory_name,
    read_positive_int_env,
)
from backend.app.main import create_app
from backend.app.project_analysis.state_manifest import (
    IncompleteProjectManifestError,
    ProjectManifestLimits,
    build_project_state_manifest,
)
from tests.test_chat_native_canonical_project_phase10 import TASK, _connect, _project


def test_generated_dot_directories_are_excluded_before_budget_accounting(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    run_history = project / "benchmarks" / ".runs"
    run_history.mkdir(parents=True)
    for index in range(50):
        (run_history / f"run_{index}.json").write_text("{}", encoding="utf-8")

    scan = build_inventory(project, limits=FolderScanLimits(
        max_files=5, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
    ))

    assert scan["complete"] is True
    assert scan["diagnostics"]["eligible_omitted"] == 0
    assert scan["diagnostics"]["total_eligible"] == 1
    by_path = {item["relative_path"]: item for item in scan["inventory"]}
    assert by_path["app.py"]["status"] == "readable"
    assert "benchmarks/.runs/run_0.json" not in by_path
    assert by_path["benchmarks/.runs"]["ignore_reason"] == "ignored_directory"


@pytest.mark.parametrize(
    "name", ["checkpoints", ".qa", ".work", "logs", "htmlcov", ".runs", ".tox", ".nox", ".pytest_cache"],
)
def test_named_generated_directories_are_ignored(name: str) -> None:
    assert is_ignored_directory_name(name) is True


@pytest.mark.parametrize("name", [".github", ".devcontainer", ".vscode"])
def test_meaningful_hidden_configuration_directories_are_never_ignored(name: str) -> None:
    assert is_ignored_directory_name(name) is False


@pytest.mark.parametrize("name", [".anything_hidden", ".claude", ".continue", ".mytool"])
def test_unrecognized_hidden_directories_are_not_silently_treated_as_generated(name: str) -> None:
    assert is_ignored_directory_name(name) is False


@pytest.mark.parametrize("name", ["backend", "frontend", "tests", "src"])
def test_ordinary_source_directory_names_are_not_ignored(name: str) -> None:
    assert is_ignored_directory_name(name) is False


def test_file_count_budget_truncation_reports_typed_diagnostics_and_marks_incomplete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for index in range(10):
        (project / f"module_{index}.py").write_text("value = 1\n", encoding="utf-8")

    scan = build_inventory(project, limits=FolderScanLimits(
        max_files=3, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
    ))

    assert scan["complete"] is False
    assert scan["diagnostics"]["file_count_budget_exceeded"] is True
    assert scan["diagnostics"]["eligible_omitted"] == 7
    assert scan["diagnostics"]["total_eligible"] == 10
    omitted = [item for item in scan["inventory"] if item.get("ignore_reason") == "file_count_budget_exceeded"]
    assert len(omitted) == 7
    assert all(item["status"] == "ignored" for item in omitted)


def test_dataset_exempt_content_does_not_consume_the_file_count_budget(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    corpus = project / "astra_corpus"
    corpus.mkdir()
    for index in range(20):
        (corpus / f"doc_{index}.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    scan = build_inventory(project, limits=FolderScanLimits(
        max_files=2, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
    ))

    assert scan["complete"] is True
    assert scan["diagnostics"]["exempt_dataset_files"] == 20
    assert scan["diagnostics"]["total_eligible"] == 1
    by_path = {item["relative_path"]: item for item in scan["inventory"]}
    assert all(by_path[f"astra_corpus/doc_{index}.csv"]["status"] == "readable" for index in range(20))
    assert is_budget_exempt_dataset_content("astra_corpus/doc_0.csv", ".csv") is True
    assert is_budget_exempt_dataset_content("backend/app.py", ".py") is False


def test_github_workflows_are_scanned_and_included(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    workflows = project / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI\non: [push]\n", encoding="utf-8")

    scan = build_inventory(project)

    by_path = {item["relative_path"]: item for item in scan["inventory"]}
    assert by_path[".github/workflows/ci.yml"]["status"] == "readable"
    assert scan["diagnostics"]["total_eligible"] == 1
    assert scan["complete"] is True


def test_devcontainer_configuration_is_scanned(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    devcontainer = project / ".devcontainer"
    devcontainer.mkdir()
    (devcontainer / "devcontainer.json").write_text('{"name": "astra"}\n', encoding="utf-8")

    scan = build_inventory(project)

    by_path = {item["relative_path"]: item for item in scan["inventory"]}
    assert by_path[".devcontainer/devcontainer.json"]["status"] == "readable"
    assert scan["diagnostics"]["total_eligible"] == 1


def test_oversized_file_inside_a_meaningful_dot_directory_marks_manifest_incomplete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    workflows = project / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("x" * 2000, encoding="utf-8")

    limits = ProjectManifestLimits(
        max_files=100, max_file_size_bytes=1000, max_total_size_bytes=10_000, max_depth=12,
    )
    partial = build_project_state_manifest(project, workspace_id="w", limits=limits, require_complete=False)

    assert partial.complete is False
    assert partial.excluded_summary.get("file_size_limit") == 1
    with pytest.raises(IncompleteProjectManifestError):
        build_project_state_manifest(project, workspace_id="w", limits=limits)


def test_ignored_generated_dot_directories_do_not_make_the_manifest_incomplete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    run_history = project / "benchmarks" / ".runs"
    run_history.mkdir(parents=True)
    for index in range(20):
        (run_history / f"run_{index}.json").write_text("{}", encoding="utf-8")
    cache = project / ".pytest_cache"
    cache.mkdir()
    (cache / "state").write_text("cache state\n", encoding="utf-8")

    manifest = build_project_state_manifest(project, workspace_id="w")

    assert manifest.complete is True
    assert {entry.normalized_relative_path for entry in manifest.entries} == {"app.py"}


def test_invalid_scan_limit_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTRA_SCAN_MAX_FILES", "not-a-number")
    with pytest.raises(FolderScanConfigError):
        read_positive_int_env("ASTRA_SCAN_MAX_FILES", 1500)

    monkeypatch.setenv("ASTRA_SCAN_MAX_FILES", "0")
    with pytest.raises(FolderScanConfigError):
        read_positive_int_env("ASTRA_SCAN_MAX_FILES", 1500)

    monkeypatch.setenv("ASTRA_SCAN_MAX_FILES", "-5")
    with pytest.raises(FolderScanConfigError):
        read_positive_int_env("ASTRA_SCAN_MAX_FILES", 1500)

    monkeypatch.setenv("ASTRA_SCAN_MAX_FILES", "2000")
    assert read_positive_int_env("ASTRA_SCAN_MAX_FILES", 1500) == 2000


def test_large_repository_scan_succeeds_under_the_default_limit_once_generated_dirs_are_excluded(
    tmp_path: Path,
) -> None:
    project = tmp_path / "big_project"
    project.mkdir()
    for index in range(200):
        (project / f"module_{index}.py").write_text(f"value = {index}\n", encoding="utf-8")
    clutter = project / "benchmarks" / ".runs"
    clutter.mkdir(parents=True)
    for index in range(3000):
        (clutter / f"run_{index}.json").write_text("{}", encoding="utf-8")

    scan = build_inventory(project)

    assert scan["complete"] is True
    assert scan["summary"]["readable"] == 200
    assert scan["diagnostics"]["file_count_budget_exceeded"] is False


def test_oversized_important_source_file_marks_manifest_incomplete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x" * 20, encoding="utf-8")
    (project / "big_module.py").write_text("x" * 2000, encoding="utf-8")

    limits = ProjectManifestLimits(
        max_files=100, max_file_size_bytes=1000, max_total_size_bytes=10_000, max_depth=12,
    )
    partial = build_project_state_manifest(project, workspace_id="w", limits=limits, require_complete=False)

    assert partial.complete is False
    assert partial.excluded_summary.get("file_size_limit") == 1
    with pytest.raises(IncompleteProjectManifestError):
        build_project_state_manifest(project, workspace_id="w", limits=limits)


def test_oversized_dataset_exempt_file_does_not_mark_manifest_incomplete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    corpus = project / "astra_corpus"
    corpus.mkdir()
    (corpus / "big.csv").write_text("x" * 2000, encoding="utf-8")

    limits = ProjectManifestLimits(
        max_files=100, max_file_size_bytes=1000, max_total_size_bytes=10_000, max_depth=12,
    )
    manifest = build_project_state_manifest(project, workspace_id="w", limits=limits)

    assert manifest.complete is True
    assert manifest.excluded_summary.get("file_size_limit") == 1
    assert "astra_corpus/big.csv" not in {entry.normalized_relative_path for entry in manifest.entries}
    assert "app.py" in {entry.normalized_relative_path for entry in manifest.entries}


def test_rescan_after_raising_the_configured_limit_produces_a_complete_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for index in range(10):
        (project / f"module_{index}.py").write_text("value = 1\n", encoding="utf-8")

    tight = ProjectManifestLimits(
        max_files=3, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
    )
    with pytest.raises(IncompleteProjectManifestError):
        build_project_state_manifest(project, workspace_id="w", limits=tight)

    generous = ProjectManifestLimits(
        max_files=20, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
    )
    manifest = build_project_state_manifest(project, workspace_id="w", limits=generous)

    assert manifest.complete is True
    assert len(manifest.entries) == 10


def test_canonical_project_creation_fails_closed_with_an_actionable_message_when_manifest_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.project_delivery import service as delivery_service

    project = _project(tmp_path)
    for index in range(10):
        (project / f"extra_{index}.py").write_text("value = 1\n", encoding="utf-8")

    original = delivery_service.build_project_state_manifest
    tight = ProjectManifestLimits(
        max_files=3, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
    )
    monkeypatch.setattr(
        delivery_service, "build_project_state_manifest",
        lambda root, *, workspace_id=None, **kwargs: original(root, workspace_id=workspace_id, limits=tight),
    )

    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        response = client.post(
            "/chat/run", json={"message": TASK, "conversation_id": conversation_id, "use_rag": True},
        )

    assert response.status_code == 409
    body = response.json()
    message = body["detail"]["message"].lower()
    assert "incomplete" in message
    assert "increase a safe limit and rescan" in message


def test_canonical_project_creation_succeeds_after_a_complete_rescan_once_limit_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.project_delivery import service as delivery_service

    project = _project(tmp_path)
    for index in range(10):
        (project / f"extra_{index}.py").write_text("value = 1\n", encoding="utf-8")

    original = delivery_service.build_project_state_manifest
    active_limits = {
        "value": ProjectManifestLimits(
            max_files=3, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
        ),
    }
    monkeypatch.setattr(
        delivery_service, "build_project_state_manifest",
        lambda root, *, workspace_id=None, **kwargs: original(
            root, workspace_id=workspace_id, limits=active_limits["value"],
        ),
    )

    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        blocked = client.post(
            "/chat/run", json={"message": TASK, "conversation_id": conversation_id, "use_rag": True},
        )
        assert blocked.status_code == 409

        active_limits["value"] = ProjectManifestLimits(
            max_files=100, max_file_size_bytes=5_000_000, max_total_size_bytes=5_000_000, max_depth=12,
        )
        created = client.post(
            "/chat/run", json={"message": TASK, "conversation_id": conversation_id, "use_rag": True},
        )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action"]["action_type"] == "canonical_project"
    assert body["action"]["project"]["pending_user_action"] == "approve_plan"
