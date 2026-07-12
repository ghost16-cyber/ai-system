from pathlib import Path

import pytest

from backend.app.folders.patches import (
    ProjectPatchError,
    apply_project_patch,
    create_patch_proposal,
    public_patch_proposal,
    rollback_project_patch,
    validate_patch_fresh,
    verify_patch_approval,
)


def _proposal(root: Path, changes: list[dict]) -> dict:
    return create_patch_proposal(
        root=root,
        conversation_id="conversation-1",
        folder_access_id="access-1",
        user_request="Make the safe change",
        changes=changes,
        files_inspected=[],
    )


def test_patch_preview_is_immutable_and_does_not_modify_files(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    proposal = _proposal(tmp_path, [{"path": "app.py", "operation": "modify", "content": "value = 2\n"}])
    public = public_patch_proposal(proposal)
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert proposal["changes"][0]["before_hash"]
    assert proposal["changes"][0]["after_hash"]
    assert "--- a/app.py" in proposal["changes"][0]["unified_diff"]
    assert "after_content" not in public["changes"][0]
    assert proposal["file_set"] == ["app.py"]


def test_patch_requires_exact_approval_and_rejects_wrong_scope(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\n", encoding="utf-8")
    proposal = _proposal(tmp_path, [{"path": "app.py", "content": "two\n"}])
    with pytest.raises(ProjectPatchError):
        verify_patch_approval(proposal, conversation_id="other", folder_access_id="access-1", confirmation=f"APPROVE PATCH {proposal['patch_id']}")
    with pytest.raises(ProjectPatchError):
        verify_patch_approval(proposal, conversation_id="conversation-1", folder_access_id="access-1", confirmation="approve")
    verify_patch_approval(proposal, conversation_id="conversation-1", folder_access_id="access-1", confirmation=f"APPROVE PATCH {proposal['patch_id']}")


def test_patch_applies_create_modify_delete_and_rolls_back(tmp_path: Path) -> None:
    (tmp_path / "modify.txt").write_text("before\n", encoding="utf-8")
    (tmp_path / "delete.txt").write_text("restore me\n", encoding="utf-8")
    proposal = _proposal(tmp_path, [
        {"path": "modify.txt", "operation": "modify", "content": "after\n"},
        {"path": "create.txt", "operation": "create", "content": "new\n"},
        {"path": "delete.txt", "operation": "delete"},
    ])
    approved = {**proposal, "status": "approved"}
    applied, snapshot = apply_project_patch(tmp_path, approved)
    assert (tmp_path / "modify.txt").read_text(encoding="utf-8") == "after\n"
    assert (tmp_path / "create.txt").read_text(encoding="utf-8") == "new\n"
    assert not (tmp_path / "delete.txt").exists()
    rolled_back = rollback_project_patch(tmp_path, {**applied, "status": "rollback_approved"}, snapshot)
    assert rolled_back["status"] == "rolled_back"
    assert (tmp_path / "modify.txt").read_text(encoding="utf-8") == "before\n"
    assert not (tmp_path / "create.txt").exists()
    assert (tmp_path / "delete.txt").read_text(encoding="utf-8") == "restore me\n"


def test_patch_stale_and_rollback_conflicts_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    proposal = _proposal(tmp_path, [{"path": "app.py", "content": "after\n"}])
    target.write_text("external\n", encoding="utf-8")
    with pytest.raises(ProjectPatchError, match="stale"):
        validate_patch_fresh(tmp_path, proposal)

    target.write_text("before\n", encoding="utf-8")
    applied, snapshot = apply_project_patch(tmp_path, {**proposal, "status": "approved"})
    target.write_text("later edit\n", encoding="utf-8")
    with pytest.raises(ProjectPatchError, match="changed"):
        rollback_project_patch(tmp_path, {**applied, "status": "rollback_approved"}, snapshot)


@pytest.mark.parametrize("path", [".env", "../escape.py", "model.pt", ".git/config"])
def test_patch_rejects_sensitive_excluded_and_outside_paths(tmp_path: Path, path: str) -> None:
    target = tmp_path / path if ".." not in path else None
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("secret\n", encoding="utf-8")
    with pytest.raises((ProjectPatchError, ValueError)):
        _proposal(tmp_path, [{"path": path, "operation": "modify", "content": "changed\n"}])
