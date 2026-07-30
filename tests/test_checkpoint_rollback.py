from __future__ import annotations

from pathlib import Path

from backend.app.orchestrator import Orchestrator, OrchestratorConfig
from backend.app.orchestrator.approvals import approve_pending_patch
from backend.app.orchestrator.models import ToolAction


class BadPatchProposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_next_action(self, state):
        self.calls += 1
        if self.calls == 1:
            return ToolAction(
                action="propose_patch",
                reason="Wrong arithmetic patch.",
                args={
                    "path": "calculator.py",
                    "old": "return a - b",
                    "new": "return a * b",
                },
            )
        if self.calls == 2:
            return ToolAction(action="apply_patch", reason="Apply patch.", args={})
        return ToolAction(
            action="run_tests",
            reason="Verify patch.",
            args={"command": "python -m pytest -q"},
        )


class ReviewPatchProposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_next_action(self, state):
        self.calls += 1
        if self.calls == 1:
            return ToolAction(
                action="propose_patch",
                reason="Correct arithmetic patch.",
                args={
                    "path": "calculator.py",
                    "old": "return a - b",
                    "new": "return a + b",
                },
            )
        return ToolAction(action="apply_patch", reason="Request apply.", args={})


def test_failed_patch_rolls_back_to_checkpoint(tmp_path: Path):
    _write_calculator_project(tmp_path)
    original = (tmp_path / "calculator.py").read_text(encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=BadPatchProposer(),
        config=OrchestratorConfig(
            max_steps=5,
            checkpoint_root=str(tmp_path / "checkpoints"),
        ),
    ).run(goal="Fix calculator", allow_edits=True)

    assert result.status == "failed"
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == original
    assert result.trace["validation"]["checkpoint"]["checkpoint_path"]
    assert result.trace["validation"]["rollback"]["restored"] is True
    assert "Tests failed after patch" in result.final_response


def test_review_mode_saves_pending_patch_and_approval_applies_it(tmp_path: Path):
    _write_calculator_project(tmp_path)
    checkpoint_root = tmp_path / "checkpoints"
    approval_root = tmp_path / "approvals"

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=ReviewPatchProposer(),
        config=OrchestratorConfig(
            max_steps=3,
            checkpoint_root=str(checkpoint_root),
            approval_root=str(approval_root),
        ),
    ).run(
        goal="Fix calculator",
        allow_edits=True,
        approval_mode="review",
    )

    assert result.status == "needs_approval"
    assert "return a - b" in (tmp_path / "calculator.py").read_text(encoding="utf-8")
    approval = result.trace["validation"]["approval"]
    assert approval["status"] == "pending"

    approved = approve_pending_patch(
        approval_root=approval_root,
        approval_id=result.task_id,
    )

    assert approved["applied"] is True
    assert approved["tests"]["status"] == "passed"
    assert "return a + b" in (tmp_path / "calculator.py").read_text(encoding="utf-8")


def _write_calculator_project(path: Path) -> None:
    (path / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (path / "test_calculator.py").write_text(
        "from calculator import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
