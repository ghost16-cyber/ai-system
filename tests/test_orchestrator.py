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


class PatchThenRepeatTestsProposer:
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
                reason="Apply patch.",
                args={},
            )
        return ToolAction(
            action="run_tests",
            reason="Keep running tests.",
            args={"command": "python -m pytest -q"},
        )


class RepeatSearchProposer:
    def propose_next_action(self, state):
        return ToolAction(
            action="search_files",
            reason="Repeat the same search.",
            args={"query": "calculator"},
        )


class RunTestsThenStopProposer:
    def __init__(self):
        self.calls = 0

    def propose_next_action(self, state):
        self.calls += 1
        if self.calls == 1:
            return ToolAction(
                action="run_tests",
                reason="Gather failing test evidence.",
                args={"command": "python -m pytest -q"},
            )
        return ToolAction(
            action="final_response",
            reason="Stop after advisors update candidates.",
            args={"message": "done"},
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


def test_orchestrator_finishes_verified_patch_success_before_repeat_guard(tmp_path: Path):
    (tmp_path / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=PatchThenRepeatTestsProposer(),
        config=OrchestratorConfig(max_steps=6, max_repeated_actions=1),
    ).run(goal="Fix calculator", allow_edits=True)

    assert result.status == "completed"
    assert result.final_response == "Patch applied and tests passed."


def test_orchestrator_stops_repeated_identical_actions(tmp_path: Path):
    (tmp_path / "calculator.py").write_text("def add():\n    return 1\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=RepeatSearchProposer(),
        config=OrchestratorConfig(max_steps=10, max_repeated_actions=2),
    ).run(goal="Find calculator")

    assert result.status == "completed"
    assert "same tool action repeated" in result.final_response
    assert [item["action"] for item in result.trace["tool_history"]] == [
        "search_files",
        "search_files",
        "read_file",
        "analyze_ast",
        "final_response",
    ]


def test_repeat_guard_runs_tests_before_repeating_same_read(tmp_path: Path):
    (tmp_path / "one.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("print(2)\n", encoding="utf-8")

    class RepeatReadProposer:
        def propose_next_action(self, state):
            return ToolAction(
                action="read_file",
                reason="Keep reading the same file.",
                args={"path": "one.py"},
            )

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=RepeatReadProposer(),
        config=OrchestratorConfig(max_steps=4, max_repeated_actions=1),
    ).run(goal="Inspect one two")

    assert result.trace["tool_history"][1]["action"] == "run_tests"


def test_search_ignores_data_and_venv_prefixes(tmp_path: Path):
    (tmp_path / "testing").mkdir()
    (tmp_path / "testing" / "calculator.py").write_text(
        "def add():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "test.py").write_text("print('junk')\n", encoding="utf-8")
    site_packages = tmp_path / "vend_web" / ".venv312" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    (site_packages / "test_colorama.py").write_text("print('junk')\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=RepeatSearchProposer(),
        config=OrchestratorConfig(max_steps=1),
    ).run(goal="Fix testing calculator")

    matches = result.trace["tool_history"][0]["output"]["matches"]
    paths = [item["path"] for item in matches]
    assert "testing/calculator.py" in paths
    assert all(not path.startswith("data/") for path in paths)
    assert all(".venv312" not in path for path in paths)


def test_search_ignores_site_packages_and_binary_suffixes(tmp_path: Path):
    (tmp_path / "calculator.py").write_text("def add():\n    return 1\n", encoding="utf-8")
    site_packages = tmp_path / ".venv" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    (site_packages / "calculator.py").write_text("junk\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("calculator\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=RepeatSearchProposer(),
        config=OrchestratorConfig(max_steps=1),
    ).run(goal="Find calculator")

    matches = result.trace["tool_history"][0]["output"]["matches"]
    assert [item["path"] for item in matches] == ["calculator.py"]


def test_file_relevance_prioritizes_failing_test_imports(tmp_path: Path):
    (tmp_path / "sequence_utils.py").write_text(
        "def count_items(values: list[int]) -> int:\n"
        "    return len(values) - 1\n",
        encoding="utf-8",
    )
    (tmp_path / "test_sequence_utils.py").write_text(
        "from sequence_utils import count_items\n\n"
        "def test_count_items():\n"
        "    assert count_items([1, 2, 3]) == 3\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text("print('noise')\n", encoding="utf-8")

    result = Orchestrator(
        workspace_root=tmp_path,
        proposer=RunTestsThenStopProposer(),
        config=OrchestratorConfig(max_steps=2),
    ).run(goal="Fix the failing list length test")

    assert result.trace["candidate_files"][:2] == [
        "test_sequence_utils.py",
        "sequence_utils.py",
    ]
    assert "unrelated.py" not in result.trace["candidate_files"][:3]


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
