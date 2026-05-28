from __future__ import annotations

from typing import Protocol

from .models import TaskState, ToolAction


class ActionProposer(Protocol):
    def propose_next_action(self, state: TaskState) -> ToolAction:
        """Return the next requested tool action.

        A future SLM adapter should implement this protocol and return the same
        structured ToolAction object. The orchestrator remains the execution
        authority.
        """


class ScriptedActionProposer:
    """Deterministic proposer used until a local SLM is connected.

    This keeps the orchestrator executable and testable without pretending that
    rule code is the final reasoning layer.
    """

    def propose_next_action(self, state: TaskState) -> ToolAction:
        if not state.tool_history:
            if state.intent in {"debug_error", "write_tests"} or _mentions_tests(state.goal):
                return ToolAction(
                    action="run_tests",
                    reason="The task appears test/debug oriented, so gather test evidence first.",
                    args={"command": "python -m pytest -q"},
                )
            return ToolAction(
                action="search_files",
                reason="Start by finding files relevant to the request.",
                args={"query": state.goal, "max_results": 10},
            )

        last = state.tool_history[-1]
        if not last.allowed:
            return ToolAction(
                action="final_response",
                reason="A policy boundary blocked the last action.",
                args={
                    "message": (
                        "I stopped because the requested action was blocked by policy: "
                        f"{last.policy_reason or last.error}"
                    )
                },
            )

        if _has_passing_tests(state) and state.proposed_patch is None:
            return ToolAction(
                action="final_response",
                reason="Tests already pass and no patch was proposed.",
                args={
                    "message": "The verification command passed. No patch was proposed by the scripted proposer."
                },
            )

        if not _used(state, "search_files"):
            query = _latest_test_output(state) or state.goal
            return ToolAction(
                action="search_files",
                reason="Use the current evidence to find likely relevant files.",
                args={"query": query, "max_results": 10},
            )

        next_file = _next_candidate_to_read(state)
        if next_file:
            return ToolAction(
                action="read_file",
                reason="Inspect the highest-ranked candidate file.",
                args={"path": next_file},
            )

        next_ast_file = _next_python_file_for_ast(state)
        if next_ast_file:
            return ToolAction(
                action="analyze_ast",
                reason="Extract Python structure from an inspected source file.",
                args={"path": next_ast_file},
            )

        return ToolAction(
            action="final_response",
            reason="The scripted proposer has gathered available evidence.",
            args={"message": _summarize(state)},
        )


def _mentions_tests(goal: str) -> bool:
    lowered = goal.lower()
    return any(token in lowered for token in ("test", "pytest", "failing", "failure"))


def _used(state: TaskState, action: str) -> bool:
    return any(result.action == action for result in state.tool_history)


def _has_passing_tests(state: TaskState) -> bool:
    tests = state.validation.tests or {}
    return tests.get("status") == "passed"


def _latest_test_output(state: TaskState) -> str | None:
    for result in reversed(state.tool_history):
        if result.action == "run_tests":
            output = result.output.get("output")
            return str(output) if output else None
    return None


def _next_candidate_to_read(state: TaskState) -> str | None:
    for candidate in state.candidate_files:
        if candidate not in state.inspected_files:
            return candidate
    return None


def _next_python_file_for_ast(state: TaskState) -> str | None:
    ast_paths = {
        str(result.output.get("path"))
        for result in state.tool_history
        if result.action == "analyze_ast" and result.success
    }
    for path in state.inspected_files:
        if path.endswith(".py") and path not in ast_paths:
            return path
    return None


def _summarize(state: TaskState) -> str:
    parts = [f"Intent: {state.intent or 'unknown'}."]
    if state.validation.tests:
        tests = state.validation.tests
        parts.append(
            f"Tests {tests.get('status')} with exit code {tests.get('exit_code')}."
        )
    if state.inspected_files:
        parts.append(f"Inspected files: {', '.join(state.inspected_files)}.")
    if state.proposed_patch:
        parts.append("A patch was proposed but not automatically applied.")
    else:
        parts.append("No patch was proposed by the current proposer.")
    return " ".join(parts)
