from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .advisors import Advisor, build_default_advisors
from .models import OrchestratorConfig, OrchestratorResult, TaskState, ToolResult
from .policy import PolicyError, SafetyPolicy
from .proposers import ActionProposer, ScriptedActionProposer
from .tools import ToolRegistry, build_default_tool_registry
from .trace_store import TraceStore, to_public_trace


class Orchestrator:
    """Controlled task loop for SLM/advisor/tool collaboration.

    The orchestrator is intentionally model-agnostic: SLMs and future DL
    advisors plug in through small interfaces, while this class owns policy,
    loop limits, state updates, and trace persistence.
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        proposer: ActionProposer | None = None,
        advisors: list[Advisor] | None = None,
        tools: ToolRegistry | None = None,
        trace_store: TraceStore | None = None,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.proposer = proposer or ScriptedActionProposer()
        self.advisors = advisors if advisors is not None else build_default_advisors()
        self.tools = tools or build_default_tool_registry()
        self.trace_store = trace_store
        self.config = config or OrchestratorConfig()

    def run(
        self,
        *,
        goal: str,
        project_path: str = ".",
        allow_edits: bool = False,
        allow_tests: bool = True,
        task_id: str | None = None,
    ) -> OrchestratorResult:
        try:
            policy = SafetyPolicy(self.workspace_root, project_path)
        except PolicyError as error:
            state = TaskState(
                task_id=task_id or str(uuid4()),
                goal=goal,
                workspace=str(self.workspace_root),
                project_path=project_path,
                status="blocked",
                stop_reason=str(error),
                final_response=f"Task stopped by policy: {error}",
                allow_edits=allow_edits,
                allow_tests=allow_tests,
            )
            return self._finish(state)

        state = TaskState(
            **({"task_id": task_id} if task_id else {}),
            goal=goal,
            workspace=str(self.workspace_root),
            project_path=project_path,
            allow_edits=allow_edits,
            allow_tests=allow_tests,
        )

        self._run_advisors(state, policy)

        while state.status == "running" and state.step_count < self.config.max_steps:
            state.step_count += 1
            action = self.proposer.propose_next_action(state)
            result = self.tools.execute(action, state, policy)
            self._apply_result_effects(state, result)
            state.record_tool_result(result)

            if action.action == "final_response":
                break
            if not result.allowed:
                state.status = "blocked"
                state.stop_reason = result.policy_reason or result.error
                break
            if self.config.auto_run_advisors_each_step:
                self._run_advisors(state, policy)

        if state.status == "running":
            state.status = "max_steps_reached"
            state.stop_reason = f"Reached max_steps={self.config.max_steps}."
        if state.final_response is None:
            state.final_response = self._fallback_final_response(state)

        return self._finish(state)

    def _run_advisors(self, state: TaskState, policy: SafetyPolicy) -> None:
        for advisor in self.advisors:
            try:
                output = advisor.analyze(state, policy)
            except Exception as error:
                from .models import AdvisorOutput

                output = AdvisorOutput(
                    name=getattr(advisor, "name", advisor.__class__.__name__),
                    label="advisor_error",
                    confidence=0.0,
                    reason=f"{type(error).__name__}: {error}",
                )
            state.record_advisor(output)

    def _apply_result_effects(self, state: TaskState, result: ToolResult) -> None:
        if result.action == "search_files" and result.success:
            for match in result.output.get("matches", []):
                path = match.get("path") if isinstance(match, dict) else None
                if isinstance(path, str) and path not in state.candidate_files:
                    state.candidate_files.append(path)

    def _finish(self, state: TaskState) -> OrchestratorResult:
        if self.trace_store is not None:
            self.trace_store.append(state)
        return OrchestratorResult(
            task_id=state.task_id,
            status=state.status,
            final_response=state.final_response,
            stop_reason=state.stop_reason,
            trace=to_public_trace(state),
        )

    def _fallback_final_response(self, state: TaskState) -> str:
        if state.status == "blocked":
            return f"Task stopped by policy: {state.stop_reason}"
        if state.validation.tests:
            tests = state.validation.tests
            return (
                f"Task stopped with test status {tests.get('status')} "
                f"(exit code {tests.get('exit_code')})."
            )
        return f"Task stopped with status: {state.status}."
