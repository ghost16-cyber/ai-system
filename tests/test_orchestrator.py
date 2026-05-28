from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.jobs import LocalWorker, build_job_handlers
from backend.app.main import create_app
from backend.app.orchestrator import Orchestrator, OrchestratorConfig
from backend.app.orchestrator.models import ToolAction


class ReadSecretProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="read_file",
            reason="Attempt to read a blocked file.",
            args={"path": ".env"},
        )


class PatchProposer:
    def __init__(self):
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
            return ToolAction(
                action="apply_patch",
                reason="Apply the validated patch.",
                args={},
            )
        return ToolAction(
            action="final_response",
            reason="Stop after patch attempt.",
            args={"message": "Done."},
        )


def test_orchestrator_runs_scripted_loop_and_redacts_read_content(tmp_path: Path):
    (tmp_path / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    result = Orchestrator(
        workspace_root=tmp_path,
        config=OrchestratorConfig(max_steps=6),
    ).run(goal="Fix the failing tests")

    assert result.status in {"completed", "max_steps_reached"}
    assert result.trace["intent"] == "debug_error"
    assert "calculator.py" in result.trace["candidate_files"]

    read_outputs = [
        item["output"]
        for item in result.trace["tool_history"]
        if item["action"] == "read_file"
    ]
    assert read_outputs
    assert read_outputs[0]["content"]["redacted"] is True


def test_orchestrator_blocks_secret_file_reads(tmp_path: Path):
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=ReadSecretProposer(),
        config=OrchestratorConfig(max_steps=1),
    ).run(goal="Read env")

    assert result.status == "blocked"
    assert "blocked" in result.final_response.lower()
    assert result.trace["tool_history"][0]["allowed"] is False


def test_apply_patch_requires_explicit_edit_permission(tmp_path: Path):
    source = "def add(a, b):\n    return a - b\n"
    target = tmp_path / "calculator.py"
    target.write_text(source, encoding="utf-8")

    blocked = Orchestrator(
        workspace_root=tmp_path,
        proposer=PatchProposer(),
        config=OrchestratorConfig(max_steps=2),
    ).run(goal="Patch calculator", allow_edits=False)

    assert blocked.status == "blocked"
    assert target.read_text(encoding="utf-8") == source

    applied = Orchestrator(
        workspace_root=tmp_path,
        proposer=PatchProposer(),
        config=OrchestratorConfig(max_steps=3),
    ).run(goal="Patch calculator", allow_edits=True)

    assert applied.status == "completed"
    assert "return a + b" in target.read_text(encoding="utf-8")


def test_orchestrate_api_queues_worker_job(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text("print('hello')\n", encoding="utf-8")

    with TestClient(
        create_app(tmp_path / "orchestrator.db", workspace_root=workspace)
    ) as client:
        response = client.post(
            "/orchestrate",
            json={"goal": "Explain this project", "path": ".", "max_steps": 3},
        )
        assert response.status_code == 202
        queued = response.json()

        worker = LocalWorker(
            client.app.state.job_queue,
            handlers=build_job_handlers(workspace),
        )
        assert worker.run_once() is True
        completed = client.get(queued["status_url"]).json()

    assert completed["status"] == "succeeded"
    result = completed["result"]
    assert result["status"] in {"completed", "max_steps_reached"}
    assert result["trace"]["goal"] == "Explain this project"
