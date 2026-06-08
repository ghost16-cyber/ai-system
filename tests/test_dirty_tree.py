from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.orchestrator import Orchestrator, OrchestratorConfig
from backend.app.orchestrator.models import ToolAction


class PatchProposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_next_action(self, state):
        self.calls += 1
        if self.calls == 1:
            return ToolAction(
                action="propose_patch",
                reason="Replace subtraction with addition.",
                args={
                    "path": "calculator.py",
                    "old": "return a - b",
                    "new": "return a + b",
                },
            )
        if self.calls == 2:
            return ToolAction(action="apply_patch", reason="Apply patch.", args={})
        return ToolAction(
            action="final_response",
            reason="Done.",
            args={"message": "done"},
        )


def test_apply_patch_blocks_dirty_target_file(tmp_path: Path):
    _require_git()
    _make_git_repo(tmp_path)
    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a - b\n# user edit\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=PatchProposer(),
        config=OrchestratorConfig(max_steps=3),
    ).run(goal="Patch calculator", allow_edits=True)

    assert result.status == "blocked"
    assert "Dirty working tree" in result.final_response
    assert "return a + b" not in target.read_text(encoding="utf-8")
    assert result.trace["validation"]["dirty_worktree"]["target_dirty"] is True


def test_apply_patch_blocks_untracked_dirty_repo(tmp_path: Path):
    _require_git()
    _make_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("local note\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=PatchProposer(),
        config=OrchestratorConfig(max_steps=3),
    ).run(goal="Patch calculator", allow_edits=True)

    assert result.status == "blocked"
    assert "Dirty working tree" in result.final_response
    assert result.trace["validation"]["dirty_worktree"]["dirty"] is True


def test_dirty_repo_can_be_overridden_explicitly(tmp_path: Path):
    _require_git()
    _make_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("local note\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=PatchProposer(),
        config=OrchestratorConfig(max_steps=3),
    ).run(
        goal="Patch calculator",
        allow_edits=True,
        allow_dirty_worktree=True,
    )

    assert result.status == "completed"
    assert "return a + b" in (tmp_path / "calculator.py").read_text(encoding="utf-8")
    assert result.trace["validation"]["dirty_worktree"]["dirty"] is True


def _make_git_repo(path: Path) -> None:
    (path / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial")


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _require_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for dirty worktree tests")
