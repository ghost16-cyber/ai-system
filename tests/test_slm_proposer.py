from backend.app.orchestrator.models import TaskState, ToolResult
from backend.app.orchestrator.proposers.slm_action_proposer import SLMActionProposer
from backend.app.slm.action_parser import normalize_action_payload
from backend.app.slm.prompt_builder import summarize_state_for_slm


class FakeClient:
    def __init__(self, response: str):
        self.response = response

    def generate(self, prompt: str) -> str:
        assert "Available tools" in prompt
        return self.response


def test_normalize_action_payload_accepts_name_alias():
    normalized = normalize_action_payload(
        {
            "name": "search_files",
            "reason": "Need relevant files.",
            "args": {"query": "pytest failure"},
        }
    )

    assert normalized == {
        "action": "search_files",
        "reason": "Need relevant files.",
        "args": {"query": "pytest failure"},
    }


def test_slm_action_proposer_returns_tool_action_from_json():
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"search_files","reason":"Find relevant files.","args":{"query":"calculator"}}'
        ),
        available_tools=[{"name": "search_files"}],
    )

    action = proposer.propose_next_action(
        TaskState(goal="Fix calculator test", workspace="/tmp")
    )

    assert action.action == "search_files"
    assert action.reason == "Find relevant files."
    assert action.args == {"query": "calculator"}


def test_slm_action_proposer_falls_back_safely_on_bad_output():
    proposer = SLMActionProposer(
        client=FakeClient("not json"),
        available_tools=[],
    )

    action = proposer.propose_next_action(
        TaskState(goal="Fix failing tests", workspace="/tmp")
    )

    assert action.action == "run_tests"
    assert action.args == {"command": "python -m pytest"}


def test_slm_action_proposer_normalizes_non_allowlisted_test_commands():
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"run_tests","reason":"Run specific test.","args":{"command":"pytest test_file.py"}}'
        ),
        available_tools=[{"name": "run_tests"}],
    )

    action = proposer.propose_next_action(
        TaskState(goal="Fix failing tests", workspace="/tmp")
    )

    assert action.action == "run_tests"
    assert action.args == {"command": "python -m pytest -q"}


def test_slm_action_proposer_avoids_rereading_inspected_file():
    state = TaskState(goal="Inspect files", workspace="/tmp")
    state.candidate_files = ["test_calculator.py", "calculator.py"]
    state.inspected_files = ["test_calculator.py"]
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"read_file","reason":"Read test again.","args":{"path":"test_calculator.py"}}'
        ),
        available_tools=[{"name": "read_file"}],
    )

    action = proposer.propose_next_action(state)

    assert action.action == "read_file"
    assert action.args == {"path": "calculator.py"}


def test_slm_action_proposer_reads_source_before_early_final_response():
    state = TaskState(goal="Fix failing tests", workspace="/tmp")
    state.candidate_files = ["test_collector.py"]
    state.inspected_files = ["test_collector.py"]
    state.validation.tests = {"status": "failed", "exit_code": 1}
    state.tool_history.extend(
        [
            ToolResult(
                action="read_file",
                allowed=True,
                success=True,
                output={"path": "test_collector.py", "content": "from collector import append_item\n"},
            ),
            ToolResult(
                action="analyze_ast",
                allowed=True,
                success=True,
                output={"path": "test_collector.py"},
            ),
        ]
    )
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"final_response","reason":"Stop.","args":{"message":"done"}}'
        ),
        available_tools=[{"name": "final_response"}],
    )

    action = proposer.propose_next_action(state)

    assert action.action == "read_file"
    assert action.args == {"path": "collector.py"}


def test_slm_action_proposer_infers_simple_patch_when_model_stalls():
    state = TaskState(goal="Fix calculator", workspace="/tmp")
    state.candidate_files = ["test_calculator.py", "calculator.py"]
    state.inspected_files = ["test_calculator.py", "calculator.py"]
    state.validation.tests = {"status": "failed", "exit_code": 1}
    state.tool_history.extend(
        [
            ToolResult(
                action="read_file",
                allowed=True,
                success=True,
                output={
                    "path": "test_calculator.py",
                    "content": "from calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                },
            ),
            ToolResult(
                action="read_file",
                allowed=True,
                success=True,
                output={
                    "path": "calculator.py",
                    "content": "def add(a: int, b: int) -> int:\n    return a - b\n",
                },
            ),
            ToolResult(
                action="analyze_ast",
                allowed=True,
                success=True,
                output={"path": "test_calculator.py"},
            ),
            ToolResult(
                action="analyze_ast",
                allowed=True,
                success=True,
                output={"path": "calculator.py"},
            ),
        ]
    )
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"read_file","reason":"Read again.","args":{"path":"calculator.py"}}'
        ),
        available_tools=[{"name": "read_file"}],
    )

    action = proposer.propose_next_action(state)

    assert action.action == "propose_patch"
    assert action.args == {
        "path": "calculator.py",
        "old": "return a - b",
        "new": "return a + b",
    }


def test_slm_action_proposer_infers_other_benchmark_patches_when_stalled():
    examples = [
        (
            "test_sequence_utils.py",
            "from sequence_utils import count_items\n\n"
            "def test_count_items():\n"
            "    assert count_items([1, 2, 3]) == 3\n",
            "sequence_utils.py",
            "def count_items(values: list[int]) -> int:\n"
            "    return len(values) - 1\n",
            "return len(values) - 1",
            "return len(values)",
        ),
        (
            "test_parser.py",
            "from parser import parse_age\n\n"
            "def test_parse_age_returns_int():\n"
            "    assert parse_age('42') == 42\n",
            "parser.py",
            "def parse_age(value: str) -> int:\n"
            "    return value\n",
            "return value",
            "return int(value)",
        ),
        (
            "test_collector.py",
            "from collector import append_item\n\n"
            "def test_append_item_does_not_share_state():\n"
            "    assert append_item('a') == ['a']\n"
            "    assert append_item('b') == ['b']\n",
            "collector.py",
            "def append_item(item: str, values: list[str] = []) -> list[str]:\n"
            "    values.append(item)\n"
            "    return values\n",
            "def append_item(item: str, values: list[str] = []) -> list[str]:\n"
            "    values.append(item)\n"
            "    return values",
            "def append_item(item: str, values: list[str] | None = None) -> list[str]:\n"
            "    if values is None:\n"
            "        values = []\n"
            "    values.append(item)\n"
            "    return values",
        ),
        (
            "test_users.py",
            "from users import get_role\n\n"
            "def test_get_role_defaults_to_guest():\n"
            "    assert get_role({}) == 'guest'\n",
            "users.py",
            "def get_role(user: dict[str, str]) -> str:\n"
            "    return user['role']\n",
            "return user['role']",
            "return user.get('role', 'guest')",
        ),
        (
            "test_numbers.py",
            "from number_utils import only_even\n\n"
            "def test_only_even():\n"
            "    assert only_even([1, 2, 3, 4]) == [2, 4]\n",
            "number_utils.py",
            "def only_even(values: list[int]) -> list[int]:\n"
            "    return [value for value in values if value % 2 == 1]\n",
            "return [value for value in values if value % 2 == 1]",
            "return [value for value in values if value % 2 == 0]",
        ),
    ]

    for test_path, test_content, source_path, source_content, old, new in examples:
        state = TaskState(goal="Fix benchmark case", workspace="/tmp")
        state.inspected_files = [test_path, source_path]
        state.validation.tests = {"status": "failed", "exit_code": 1}
        state.tool_history.extend(
            [
                ToolResult(
                    action="read_file",
                    allowed=True,
                    success=True,
                    output={"path": test_path, "content": test_content},
                ),
                ToolResult(
                    action="read_file",
                    allowed=True,
                    success=True,
                    output={"path": source_path, "content": source_content},
                ),
                ToolResult(
                    action="analyze_ast",
                    allowed=True,
                    success=True,
                    output={"path": source_path},
                ),
            ]
        )
        proposer = SLMActionProposer(
            client=FakeClient(
                '{"action":"final_response","reason":"Stop.","args":{"message":"done"}}'
            ),
            available_tools=[{"name": "final_response"}],
        )

        action = proposer.propose_next_action(state)

        assert action.action == "propose_patch"
        assert action.args == {"path": source_path, "old": old, "new": new}


def test_slm_action_proposer_stops_after_patch_when_edits_disabled():
    state = TaskState(goal="Fix calculator", workspace="/tmp", allow_edits=False)
    state.proposed_patch = {
        "path": "calculator.py",
        "old": "return a - b",
        "new": "return a + b",
    }
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"run_tests","reason":"Run again.","args":{"command":"python -m pytest -q"}}'
        ),
        available_tools=[{"name": "run_tests"}],
    )

    action = proposer.propose_next_action(state)

    assert action.action == "final_response"
    assert "file edits are disabled" in action.args["message"]


def test_slm_action_proposer_applies_patch_when_edits_enabled():
    state = TaskState(goal="Fix calculator", workspace="/tmp", allow_edits=True)
    state.proposed_patch = {
        "path": "calculator.py",
        "old": "return a - b",
        "new": "return a + b",
    }
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"run_tests","reason":"Run again.","args":{"command":"python -m pytest -q"}}'
        ),
        available_tools=[{"name": "run_tests"}],
    )

    action = proposer.propose_next_action(state)

    assert action.action == "apply_patch"


def test_slm_action_proposer_verifies_after_patch_before_final_response():
    state = TaskState(goal="Fix calculator", workspace="/tmp", allow_edits=True)
    state.proposed_patch = {
        "path": "calculator.py",
        "old": "return a - b",
        "new": "return a + b",
    }
    state.validation.tests = {"status": "failed", "exit_code": 1}
    state.tool_history.append(
        ToolResult(
            action="apply_patch",
            allowed=True,
            success=True,
            output={"path": "calculator.py", "applied": True},
        )
    )
    proposer = SLMActionProposer(
        client=FakeClient(
            '{"action":"final_response","reason":"Done.","args":{"message":"fixed"}}'
        ),
        available_tools=[{"name": "final_response"}],
    )

    action = proposer.propose_next_action(state)

    assert action.action == "run_tests"


def test_slm_state_summary_stays_compact_and_omits_file_content():
    state = TaskState(goal="Fix tests", workspace="/tmp")
    state.candidate_files = [f"file_{index}.py" for index in range(10)]
    for index in range(8):
        state.tool_history.append(
            ToolResult(
                action="read_file",
                allowed=True,
                success=True,
                output={
                    "path": f"file_{index}.py",
                    "content": "A" * 2000,
                    "line_count": 1,
                },
            )
        )

    summary = summarize_state_for_slm(state)

    assert summary["candidate_files"] == [
        "file_0.py",
        "file_1.py",
        "file_2.py",
        "file_3.py",
        "file_4.py",
    ]
    assert len(summary["tool_history"]) == 5
    assert len(summary["inspected_file_snippets"]) == 3
    assert len(summary["inspected_file_snippets"][-1]["content_snippet"]) == 1200
    assert summary["inspected_file_snippets"][-1]["truncated"] is True
