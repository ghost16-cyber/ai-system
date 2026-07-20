from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from backend.app.folders import project_root_fingerprint
from backend.app.project_analysis.state_manifest import build_project_state_manifest
from backend.app.project_workers import (
    FileMutationEngine,
    FileMutationError,
    FileMutationErrorCode,
    FileMutationKind,
    FileOperationKind,
    FileOperationSpec,
    build_file_mutation_spec,
    calculate_expected_manifest_hash,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mutation_runtime(tmp_path: Path, *, expected_override: str | None = None):
    root = tmp_path / "project"
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "b.js").write_text("module.exports = 1;\n", encoding="utf-8")
    workspace_id = "workspace-mutation"
    before = build_project_state_manifest(root, workspace_id=workspace_id).manifest_hash

    (root / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "b.js").unlink()
    (root / "src" / "new.ts").write_text("export const value = 2;\n", encoding="utf-8")
    after = build_project_state_manifest(root, workspace_id=workspace_id).manifest_hash
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "b.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (root / "src" / "new.ts").unlink()
    assert build_project_state_manifest(root, workspace_id=workspace_id).manifest_hash == before

    operations = (
        FileOperationSpec(
            relative_path="a.py",
            operation=FileOperationKind.UPDATE,
            preimage_sha256=sha("VALUE = 1\n"),
            result_sha256=sha("VALUE = 2\n"),
            new_content="VALUE = 2\n",
        ),
        FileOperationSpec(
            relative_path="b.js",
            operation=FileOperationKind.DELETE,
            preimage_sha256=sha("module.exports = 1;\n"),
        ),
        FileOperationSpec(
            relative_path="src/new.ts",
            operation=FileOperationKind.CREATE,
            result_sha256=sha("export const value = 2;\n"),
            new_content="export const value = 2;\n",
        ),
    )
    assert calculate_expected_manifest_hash(
        root,
        workspace_id=workspace_id,
        operations=operations,
    ) == after
    spec = build_file_mutation_spec(
        project_run_id="project-1",
        execution_attempt_id="attempt-1",
        mutation_kind=FileMutationKind.PATCH,
        authority_id="patch-1",
        repository_root=str(root),
        repository_root_fingerprint=project_root_fingerprint(root),
        workspace_id=workspace_id,
        plan_revision_id="plan-1",
        scope_revision_id="scope-1",
        manifest_hash=before,
        expected_result_manifest_hash=expected_override or after,
        approved_paths=("a.py", "b.js", "src/new.ts"),
        operations=operations,
    )
    database = tmp_path / "control.db"
    journals = tmp_path / "mutation-journals"
    return root, database, journals, spec, before, after


def test_multi_file_mutation_is_exact_atomic_and_replayable(tmp_path: Path) -> None:
    root, database, journals, spec, before, after = mutation_runtime(tmp_path)
    engine = FileMutationEngine(database, journals)
    engine.initialize()

    result = engine.apply(spec)

    assert result.resulting_manifest_hash == after
    assert result.exact_replay is False
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert not (root / "b.js").exists()
    assert (root / "src" / "new.ts").read_text(encoding="utf-8") == "export const value = 2;\n"

    replay = engine.apply(spec)
    assert replay.file_mutation_id == result.file_mutation_id
    assert replay.evidence_hash == result.evidence_hash
    assert replay.exact_replay is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_file_mutation_journals").fetchone()[0] == 1

    rollback_operations = engine.build_rollback_operations(result.file_mutation_id)
    assert [item.operation for item in rollback_operations] == [
        FileOperationKind.UPDATE,
        FileOperationKind.CREATE,
        FileOperationKind.DELETE,
    ]
    assert calculate_expected_manifest_hash(
        root,
        workspace_id=spec.workspace_id,
        operations=rollback_operations,
    ) == before
    rollback_spec = build_file_mutation_spec(
        project_run_id=spec.project_run_id,
        execution_attempt_id="attempt-rollback",
        mutation_kind=FileMutationKind.ROLLBACK,
        authority_id="rollback-1",
        repository_root=spec.repository_root,
        repository_root_fingerprint=spec.repository_root_fingerprint,
        workspace_id=spec.workspace_id,
        plan_revision_id=spec.plan_revision_id,
        scope_revision_id=spec.scope_revision_id,
        manifest_hash=after,
        expected_result_manifest_hash=before,
        approved_paths=spec.approved_paths,
        operations=rollback_operations,
    )
    rollback_result = engine.apply(rollback_spec)
    assert rollback_result.resulting_manifest_hash == before
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (root / "b.js").read_text(encoding="utf-8") == "module.exports = 1;\n"
    assert not (root / "src" / "new.ts").exists()


def test_preimage_mismatch_fails_before_staging(tmp_path: Path) -> None:
    root, database, journals, spec, _before, _after = mutation_runtime(tmp_path)
    invalid = spec.model_copy(update={
        "operations": (
            spec.operations[0].model_copy(update={"preimage_sha256": "f" * 64}),
            *spec.operations[1:],
        ),
        "spec_hash": "0" * 64,
    })
    from backend.app.project_workers.mutation_contracts import calculate_file_mutation_hash
    invalid = invalid.model_copy(update={
        "spec_hash": calculate_file_mutation_hash(invalid.model_dump(mode="json"))
    })
    engine = FileMutationEngine(database, journals)
    engine.initialize()

    with pytest.raises(FileMutationError) as error:
        engine.apply(invalid)

    assert error.value.code == FileMutationErrorCode.PREIMAGE_MISMATCH
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_mid_commit_failure_rolls_back_full_file_set(tmp_path: Path) -> None:
    root, database, journals, spec, before, _after = mutation_runtime(tmp_path)
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second replacement failure")
        os.replace(source, target)

    engine = FileMutationEngine(database, journals, replace_file=fail_second)
    engine.initialize()

    with pytest.raises(FileMutationError) as error:
        engine.apply(spec)

    assert error.value.code == FileMutationErrorCode.COMMIT_FAILED
    assert build_project_state_manifest(root, workspace_id=spec.workspace_id).manifest_hash == before
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (root / "b.js").is_file()
    assert not (root / "src" / "new.ts").exists()
    with pytest.raises(FileMutationError) as replay_error:
        engine.apply(spec)
    assert replay_error.value.code == FileMutationErrorCode.PERSISTENCE_CONFLICT


def test_cancellation_before_commit_leaves_repository_unchanged(tmp_path: Path) -> None:
    root, database, journals, spec, before, _after = mutation_runtime(tmp_path)
    engine = FileMutationEngine(database, journals)
    engine.initialize()

    with pytest.raises(FileMutationError) as error:
        engine.apply(spec, cancel_requested=lambda: True)

    assert error.value.code == FileMutationErrorCode.CANCELLED
    assert build_project_state_manifest(root, workspace_id=spec.workspace_id).manifest_hash == before


def test_restart_recovers_interrupted_commit_from_durable_snapshots(tmp_path: Path) -> None:
    root, database, journals, spec, before, _after = mutation_runtime(tmp_path)
    calls = 0

    def simulate_process_loss(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("simulated process loss")
        os.replace(source, target)

    interrupted = FileMutationEngine(database, journals, replace_file=simulate_process_loss)
    interrupted.initialize()
    with pytest.raises(KeyboardInterrupt):
        interrupted.apply(spec)
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    restarted = FileMutationEngine(database, journals)
    restarted.initialize()
    assert restarted.recover_incomplete() == (spec.file_mutation_id,)
    assert build_project_state_manifest(root, workspace_id=spec.workspace_id).manifest_hash == before
    assert (root / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (root / "b.js").is_file()
    assert not (root / "src" / "new.ts").exists()


def test_result_manifest_mismatch_rolls_back(tmp_path: Path) -> None:
    root, database, journals, spec, before, _after = mutation_runtime(
        tmp_path,
        expected_override="e" * 64,
    )
    engine = FileMutationEngine(database, journals)
    engine.initialize()

    with pytest.raises(FileMutationError) as error:
        engine.apply(spec)

    assert error.value.code == FileMutationErrorCode.RESULT_MANIFEST_MISMATCH
    assert build_project_state_manifest(root, workspace_id=spec.workspace_id).manifest_hash == before
