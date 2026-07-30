from __future__ import annotations

from pathlib import Path
import ast
import json
from uuid import uuid4

from .advisors import Advisor, build_default_advisors
from .checkpoints import rollback_checkpoint
from ..local_runtime import build_runtime_context
from .models import (
    ApprovalMode,
    AdvisorOutput,
    OrchestratorConfig,
    OrchestratorResult,
    TaskState,
    ToolAction,
    ToolResult,
)
from .policy import PolicyError, SafetyPolicy
from .proposers import ActionProposer, build_action_proposer
from .tools import ToolRegistry, build_default_tool_registry, get_slm_tool_schemas
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
        self.config = config or OrchestratorConfig()
        self.proposer = proposer or build_action_proposer(
            self.config,
            get_slm_tool_schemas(),
        )
        self.advisors = (
            advisors
            if advisors is not None
            else build_default_advisors(self.config.advisor_runtime_mode)
        )
        self.tools = tools or build_default_tool_registry()
        self.trace_store = trace_store

    def run(
        self,
        *,
        goal: str,
        project_path: str = ".",
        allow_edits: bool = False,
        allow_tests: bool = True,
        approval_mode: ApprovalMode | None = None,
        allow_dirty_worktree: bool = False,
        rollback_on_test_failure: bool = True,
        max_patch_changed_lines: int = 20,
        allowed_patch_files: list[str] | None = None,
        task_id: str | None = None,
    ) -> OrchestratorResult:
        resolved_approval_mode = approval_mode or ("auto" if allow_edits else "never")
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
                approval_mode=resolved_approval_mode,
                allow_dirty_worktree=allow_dirty_worktree,
                rollback_on_test_failure=rollback_on_test_failure,
                max_patch_changed_lines=max_patch_changed_lines,
                allowed_patch_files=allowed_patch_files or [],
                checkpoint_root=self.config.checkpoint_root,
                approval_root=self.config.approval_root,
            )
            return self._finish(state)

        state = TaskState(
            **({"task_id": task_id} if task_id else {}),
            goal=goal,
            workspace=str(self.workspace_root),
            project_path=project_path,
            allow_edits=allow_edits,
            allow_tests=allow_tests,
            approval_mode=resolved_approval_mode,
            allow_dirty_worktree=allow_dirty_worktree,
            rollback_on_test_failure=rollback_on_test_failure,
            max_patch_changed_lines=max_patch_changed_lines,
            allowed_patch_files=allowed_patch_files or [],
            checkpoint_root=self.config.checkpoint_root,
            approval_root=self.config.approval_root,
        )

        self._record_local_runtime_context(state, policy)
        self._run_advisors(state, policy)

        while state.status == "running" and state.step_count < self.config.max_steps:
            state.step_count += 1
            action = self.proposer.propose_next_action(state)
            action = self._maybe_apply_guarded_action_override(state, action, policy)
            action = self._guard_runtime_plan_action(state, action)
            action = self._guard_repeated_action(state, action)
            audit_index = self._record_advisor_action_audit(state, action)
            result = self.tools.execute(action, state, policy)
            result.output.setdefault(
                "_requested_action_signature",
                _action_signature(action),
            )
            self._apply_result_effects(state, result)
            state.record_tool_result(result)
            self._complete_advisor_action_audit(state, audit_index, result)
            self._record_repair_trace_event(state, action, result)
            self._rollback_failed_patch_if_needed(state, result)

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
        self._apply_advisor_file_ranking_boost(state)

    def _record_local_runtime_context(
        self,
        state: TaskState,
        policy: SafetyPolicy,
    ) -> None:
        try:
            context = build_runtime_context(
                task=state.goal,
                workspace_root=policy.project_root,
            )
            output = AdvisorOutput(
                name="local_runtime",
                label=context.task_optimization.task_type,
                confidence=0.80,
                data=context.slm_context,
                reason=(
                    "Local runtime hardware, tool, and task optimization context "
                    "for SLM planning."
                ),
            )
        except Exception as error:
            output = AdvisorOutput(
                name="local_runtime",
                label="unavailable",
                confidence=0.0,
                data={"error": f"{type(error).__name__}: {error}"},
                reason="Local runtime context could not be built.",
            )
        state.record_advisor(output)

    def _apply_advisor_file_ranking_boost(self, state: TaskState) -> None:
        if self.config.advisor_runtime_mode not in {"ranking_boost", "guarded_action"}:
            return
        signal = _latest_repair_runtime_signal(state)
        if signal is None:
            return
        source_file = signal.data.get("source_file")
        confidence = signal.data.get("source_file_confidence")
        if not isinstance(source_file, str) or not source_file:
            return
        if not isinstance(confidence, (int, float)) or confidence < 0.60:
            return
        if source_file not in state.candidate_files:
            state.record_advisor(
                AdvisorOutput(
                    name="advisor_runtime_ranking",
                    label="boost_skipped",
                    confidence=float(confidence),
                    data={
                        "source_file": source_file,
                        "reason": "advisor_source_file_not_in_existing_candidates",
                    },
                    reason="Advisor ranking boost cannot introduce a new candidate file.",
                )
            )
            return

        before = list(state.candidate_files)
        old_index = state.candidate_files.index(source_file)
        shift = 2 if confidence >= 0.85 else 1
        new_index = max(0, old_index - shift)
        if new_index == old_index:
            return
        state.candidate_files.pop(old_index)
        state.candidate_files.insert(new_index, source_file)
        state.record_advisor(
            AdvisorOutput(
                name="advisor_runtime_ranking",
                label="boost_applied",
                confidence=float(confidence),
                data={
                    "source_file": source_file,
                    "before": before,
                    "after": list(state.candidate_files),
                    "old_index": old_index,
                    "new_index": new_index,
                    "boost_type": "small_existing_candidate_nudge",
                },
                reason="Advisor source-file prediction nudged an existing candidate ranking.",
            )
        )

    def _maybe_apply_guarded_action_override(
        self,
        state: TaskState,
        action: ToolAction,
        policy: SafetyPolicy,
    ) -> ToolAction:
        if self.config.advisor_runtime_mode != "guarded_action":
            return action
        signal = _latest_repair_runtime_signal(state)
        if signal is None:
            return action
        recommendation = signal.data.get("next_action")
        if not isinstance(recommendation, dict):
            return action
        confidence = recommendation.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < 0.85:
            return action
        mapped_action = str(recommendation.get("mapped_action") or "")
        if mapped_action not in _GUARDED_ACTION_OVERRIDES:
            return action
        override = _build_guarded_override(recommendation, state, policy)
        if override is None:
            return action
        override.args["_advisor_override"] = {
            "from_action": action.action,
            "advisor_recommended": recommendation.get("next_action"),
            "confidence": float(confidence),
        }
        return override

    def _record_advisor_action_audit(
        self,
        state: TaskState,
        action: ToolAction,
    ) -> int | None:
        if self.config.advisor_runtime_mode == "off":
            return None
        signal = _latest_repair_runtime_signal(state)
        if signal is None:
            return None
        recommendation = signal.data.get("next_action")
        if not isinstance(recommendation, dict):
            return None
        override = action.args.get("_advisor_override")
        state.advisor_action_audits.append(
            {
                "step": state.step_count,
                "mode": self.config.advisor_runtime_mode,
                "advisor_recommended": recommendation.get("next_action"),
                "advisor_mapped_action": recommendation.get("mapped_action"),
                "advisor_confidence": recommendation.get("confidence"),
                "actual_action": action.action,
                "override_applied": isinstance(override, dict),
                "override": override if isinstance(override, dict) else None,
                "final_result": None,
            }
        )
        return len(state.advisor_action_audits) - 1

    def _complete_advisor_action_audit(
        self,
        state: TaskState,
        audit_index: int | None,
        result: ToolResult,
    ) -> None:
        if audit_index is None:
            return
        if audit_index >= len(state.advisor_action_audits):
            return
        state.advisor_action_audits[audit_index]["final_result"] = {
            "allowed": result.allowed,
            "success": result.success,
            "status": result.output.get("status"),
            "error": result.error,
            "policy_reason": result.policy_reason,
        }

    def _apply_result_effects(self, state: TaskState, result: ToolResult) -> None:
        if result.action == "search_files" and result.success:
            for match in result.output.get("matches", []):
                path = match.get("path") if isinstance(match, dict) else None
                if isinstance(path, str) and path not in state.candidate_files:
                    state.candidate_files.append(path)
        if result.action == "validate_runtime_plan" and result.success:
            self._apply_runtime_plan_validation(state, result)

    def _apply_runtime_plan_validation(
        self,
        state: TaskState,
        result: ToolResult,
    ) -> None:
        state.execution_profile = None
        state.validation.execution_profile = None
        decision = str(result.output.get("decision") or "")
        requested = result.output.get("requested_plan")
        recommended = result.output.get("recommended_plan")
        audit = {
            "step": state.step_count,
            "decision": decision,
            "allowed": result.output.get("allowed"),
            "reason": result.output.get("reason"),
            "blocked_signals": list(result.output.get("blocked_signals") or []),
            "requested_plan": requested if isinstance(requested, dict) else {},
            "recommended_plan": recommended if isinstance(recommended, dict) else {},
            "enforcement": None,
        }

        if decision == "allow":
            state.active_runtime_plan = (
                dict(requested) if isinstance(requested, dict) else {}
            )
            audit["enforcement"] = "requested_plan_activated"
        elif decision == "downgrade":
            state.active_runtime_plan = (
                dict(recommended) if isinstance(recommended, dict) else {}
            )
            audit["enforcement"] = "recommended_plan_activated"
        elif decision == "block":
            state.active_runtime_plan = None
            audit["enforcement"] = "task_stopped"
            state.status = "blocked"
            state.stop_reason = str(
                result.output.get("reason")
                or "Runtime policy blocked the requested plan."
            )
            state.final_response = (
                "This plan is not allowed under the current runtime policy. "
                f"{state.stop_reason}"
            )

        state.runtime_plan_audits.append(audit)

    def _guard_runtime_plan_action(
        self,
        state: TaskState,
        action: ToolAction,
    ) -> ToolAction:
        validation = state.validation.runtime_plan or {}
        if validation.get("decision") != "downgrade":
            return action
        if action.action != "validate_runtime_plan":
            return action

        requested_plan = action.args.get("requested_plan")
        if requested_plan == state.active_runtime_plan:
            return action
        if not isinstance(state.active_runtime_plan, dict):
            return action

        return ToolAction(
            action="validate_runtime_plan",
            reason=(
                "Runtime policy replaced the forbidden plan with the active "
                "downgraded plan."
            ),
            args={
                "task": action.args.get("task") or state.goal,
                "requested_plan": dict(state.active_runtime_plan),
                "_runtime_gate_enforced": True,
            },
        )

    def _rollback_failed_patch_if_needed(
        self,
        state: TaskState,
        result: ToolResult,
    ) -> None:
        if not state.rollback_on_test_failure:
            return
        if result.action != "run_tests" or result.output.get("status") != "failed":
            return
        if not _patch_applied(state):
            return
        checkpoint = state.validation.checkpoint or {}
        checkpoint_path = checkpoint.get("checkpoint_path")
        if not isinstance(checkpoint_path, str):
            return
        rollback = rollback_checkpoint(checkpoint_path)
        state.validation.rollback = rollback
        state.record_tool_result(
            ToolResult(
                action="rollback_patch",
                allowed=True,
                success=bool(rollback.get("restored")),
                output=rollback,
            )
        )
        state.status = "failed"
        state.stop_reason = "Tests failed after patch; rollback restored the checkpoint."
        state.final_response = state.stop_reason

    def _guard_repeated_action(
        self,
        state: TaskState,
        action,
    ):
        patch_verification = _guard_patch_needs_verification(state, action)
        if patch_verification is not None:
            patch_verification.args["_requested_action_signature"] = _action_signature(
                patch_verification
            )
            return patch_verification

        verified_success = _guard_verified_patch_success(state, action)
        if verified_success is not None:
            verified_success.args["_requested_action_signature"] = _action_signature(
                verified_success
            )
            return verified_success

        unknown_path = _guard_unknown_path_action(state, action)
        if unknown_path is not None:
            unknown_path.args["_requested_action_signature"] = _action_signature(
                unknown_path
            )
            return unknown_path

        repeated_read = _guard_repeated_read(state, action)
        if repeated_read is not None:
            repeated_read.args["_requested_action_signature"] = _action_signature(
                repeated_read
            )
            return repeated_read

        signature = _action_signature(action)
        previous_count = 0
        for result in state.tool_history:
            if result.output.get("_requested_action_signature") == signature:
                previous_count += 1
        if previous_count < self.config.max_repeated_actions:
            action.args["_requested_action_signature"] = signature
            return action
        from .models import ToolAction

        alternate = _alternate_action(state, action)
        if alternate is not None:
            alternate.args["_requested_action_signature"] = _action_signature(alternate)
            return alternate

        return ToolAction(
            action="final_response",
            reason="Repeated identical action guard stopped the loop.",
            args={
                "message": (
                    "I stopped because the same tool action repeated too many times. "
                    "The task needs a different next action or more specific context."
                )
            },
        )

    def _finish(self, state: TaskState) -> OrchestratorResult:
        self._finalize_repair_trace_events(state)
        if self.config.repair_trace_logging_enabled:
            _write_repair_trace_events(
                state,
                Path(self.config.repair_trace_events_dir),
            )
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

    def _record_repair_trace_event(
        self,
        state: TaskState,
        action: ToolAction,
        result: ToolResult,
    ) -> None:
        state.repair_trace_events.append(
            {
                "case_id": _trace_case_id(state),
                "task_id": state.task_id,
                "step_index": state.step_count,
                "timestamp": result.created_at.isoformat(),
                "runtime_mode": self.config.advisor_runtime_mode,
                "current_action": _previous_action(state),
                "next_action": action.action,
                "advisor_signal": _latest_repair_runtime_signal_data(state),
                "runtime_plan_decision": (
                    (state.validation.runtime_plan or {}).get("decision")
                ),
                "runtime_plan_enforced": _latest_runtime_plan_enforcement(state),
                "active_runtime_plan": state.active_runtime_plan,
                "execution_profile": _execution_profile_summary(state),
                "tool_result_summary": _tool_result_summary(result),
                "files_touched": _files_touched(state, result),
                "test_status": (state.validation.tests or {}).get("status"),
                "error_category": _event_error_category(state, result),
                "policy_blocks": _policy_blocks(state),
                "final_status": None,
            }
        )

    def _finalize_repair_trace_events(self, state: TaskState) -> None:
        failure_mode = _final_failure_mode(state)
        for event in state.repair_trace_events:
            event["final_status"] = state.status
            event["failure_mode"] = failure_mode


def _guard_repeated_read(state: TaskState, action):
    if action.action != "read_file":
        return None

    requested_path = str(action.args.get("path", ""))
    if not requested_path:
        return None

    same_reads = _read_count(state, requested_path)
    if same_reads < 1:
        return None

    return _escalate_after_repeated_read(state, requested_path)


def _guard_verified_patch_success(state: TaskState, action):
    if action.action == "final_response":
        return None
    if (state.validation.tests or {}).get("status") != "passed":
        return None
    if not any(
        result.action == "apply_patch"
        and result.success
        and result.output.get("applied") is True
        for result in state.tool_history
    ):
        return None

    from .models import ToolAction

    return ToolAction(
        action="final_response",
        reason="Patch was applied and tests passed.",
        args={"message": "Patch applied and tests passed."},
    )


def _patch_applied(state: TaskState) -> bool:
    return any(
        result.action == "apply_patch"
        and result.success
        and result.output.get("applied") is True
        for result in state.tool_history
    )


def _guard_patch_needs_verification(state: TaskState, action):
    if not _patch_applied(state):
        return None
    if not state.validation.tests:
        return None
    if state.validation.tests.get("status") == "passed":
        return None

    from .models import ToolAction

    return ToolAction(
        action="run_tests",
        reason="Patch was applied; running tests to verify it before stopping.",
        args={"command": "python -m pytest -q"},
    )


def _guard_unknown_path_action(state: TaskState, action):
    if action.action not in {"read_file", "analyze_ast"}:
        return None
    requested_path = str(action.args.get("path", ""))
    if not requested_path:
        return _infer_patch_action(state)
    known_paths = set(state.candidate_files) | set(state.inspected_files)
    imported = _imported_source_candidates_from_read_files(state)
    known_paths.update(imported)
    if requested_path in known_paths:
        return None

    from .models import ToolAction

    for candidate in imported:
        if candidate not in state.inspected_files:
            return ToolAction(
                action="read_file",
                reason="Unknown path replaced with an imported source module.",
                args={"path": candidate},
            )
    inferred_patch = _infer_patch_action(state)
    if inferred_patch is not None:
        return inferred_patch
    if action.action == "analyze_ast":
        for path in state.inspected_files:
            if path.endswith(".py") and not _was_ast_analyzed(state, path):
                return ToolAction(
                    action="analyze_ast",
                    reason="Unknown AST path replaced with an inspected Python file.",
                    args={"path": path},
                )
    return None


def _read_count(state: TaskState, path: str) -> int:
    count = 0
    for result in state.tool_history:
        if result.action != "read_file":
            continue
        if result.output.get("path") == path:
            count += 1
    return count


def _escalate_after_repeated_read(state: TaskState, requested_path: str):
    from .models import ToolAction

    if not state.validation.tests:
        return ToolAction(
            action="run_tests",
            reason=(
                f"Repeated read_file for {requested_path} blocked; "
                "running tests to gather failure evidence."
            ),
            args={"command": "python -m pytest -q"},
        )

    if requested_path.endswith(".py") and not _was_ast_analyzed(state, requested_path):
        return ToolAction(
            action="analyze_ast",
            reason=(
                f"Repeated read_file for {requested_path} blocked; "
                "analyzing its AST instead."
            ),
            args={"path": requested_path},
        )

    for path in state.inspected_files:
        if path.endswith(".py") and not _was_ast_analyzed(state, path):
            return ToolAction(
                action="analyze_ast",
                reason=(
                    "Repeated read_file blocked; analyzing another inspected "
                    "Python file instead."
                ),
                args={"path": path},
            )

    inferred_patch = _infer_patch_action(state)
    if inferred_patch is not None:
        return inferred_patch

    return ToolAction(
        action="final_response",
        reason="Repeated read_file guard stopped the loop after available evidence.",
        args={
            "message": (
                "I stopped because the model repeatedly requested a file that was "
                "already inspected. Tests and AST evidence were already gathered."
            )
        },
    )


def _action_signature(action) -> str:
    args = {
        key: value
        for key, value in action.args.items()
        if key not in {"_requested_action_signature", "_advisor_override"}
    }
    return json.dumps(
        {"action": action.action, "args": args},
        sort_keys=True,
        default=str,
    )


def _alternate_action(state: TaskState, action):
    from .models import ToolAction

    if action.action in {"search_files", "read_file"}:
        requested_path = str(action.args.get("path", ""))
        for candidate in _imported_source_candidates_from_read_files(state):
            if candidate not in state.inspected_files:
                return ToolAction(
                    action="read_file",
                    reason=(
                        "Repeat guard followed an imported source module before "
                        "stopping."
                    ),
                    args={"path": candidate},
                )
        for candidate in state.candidate_files:
            if candidate == requested_path:
                continue
            if candidate in state.inspected_files:
                continue
            return ToolAction(
                action="read_file",
                reason="Repeat guard selected the next uninspected candidate file.",
                args={"path": candidate},
            )
        for path in state.inspected_files:
            if path.endswith(".py") and not _was_ast_analyzed(state, path):
                return ToolAction(
                    action="analyze_ast",
                    reason="Repeat guard switched from repeated file reads to AST analysis.",
                    args={"path": path},
                )
        if _goal_mentions_tests(state.goal) and not state.validation.tests:
            return ToolAction(
                action="run_tests",
                reason="Repeat guard switched from repeated file reads to test execution.",
                args={"command": "python -m pytest -q"},
            )
        inferred_patch = _infer_patch_action(state)
        if inferred_patch is not None:
            return inferred_patch

    if action.action == "analyze_ast":
        requested_path = str(action.args.get("path", ""))
        if requested_path not in state.inspected_files:
            inferred_patch = _infer_patch_action(state)
            if inferred_patch is not None:
                return inferred_patch
            for path in state.inspected_files:
                if path.endswith(".py") and not _was_ast_analyzed(state, path):
                    return ToolAction(
                        action="analyze_ast",
                        reason=(
                            "Repeat guard replaced an unknown AST path with an "
                            "inspected Python file."
                        ),
                        args={"path": path},
                    )
        for path in state.inspected_files:
            if path == requested_path or _was_ast_analyzed(state, path) or not path.endswith(".py"):
                continue
            return ToolAction(
                action="analyze_ast",
                reason="Repeat guard selected the next inspected Python file for AST analysis.",
                args={"path": path},
            )

    if action.action == "run_tests":
        for candidate in _imported_source_candidates_from_read_files(state):
            if candidate not in state.inspected_files:
                return ToolAction(
                    action="read_file",
                    reason=(
                        "Repeat guard followed an imported source module after "
                        "repeated test failures."
                    ),
                    args={"path": candidate},
                )
        for candidate in state.candidate_files:
            if candidate not in state.inspected_files:
                return ToolAction(
                    action="read_file",
                    reason="Repeat guard switched from repeated tests to inspecting candidate files.",
                    args={"path": candidate},
                )
        if not state.candidate_files:
            return ToolAction(
                action="search_files",
                reason="Repeat guard switched from repeated tests to file search.",
                args={"query": state.goal, "max_results": 10},
            )
        inferred_patch = _infer_patch_action(state)
        if inferred_patch is not None:
            return inferred_patch

    if action.action == "propose_patch" and state.proposed_patch:
        return ToolAction(
            action="final_response",
            reason="Repeat guard stopped after a patch was already proposed.",
            args={"message": "A patch has already been proposed; I stopped before repeating it."},
        )

    return None


def _was_ast_analyzed(state: TaskState, path: str) -> bool:
    return any(
        result.action == "analyze_ast"
        and result.success
        and result.output.get("path") == path
        for result in state.tool_history
    )


def _goal_mentions_tests(goal: str) -> bool:
    lowered = goal.lower()
    return any(token in lowered for token in ("test", "pytest", "failing", "failure"))


def _imported_source_candidates_from_read_files(state: TaskState) -> list[str]:
    candidates: list[str] = []
    for result in state.tool_history:
        if result.action != "read_file" or not result.success:
            continue
        content = result.output.get("content")
        if not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.split(".")[0] in _STDLIB_OR_EXTERNAL_MODULES:
                    continue
                for candidate in _module_path_candidates(module):
                    if candidate not in candidates:
                        candidates.append(candidate)
    return candidates


def _module_path_candidates(module: str) -> list[str]:
    parts = [part for part in module.split(".") if part]
    if not parts:
        return []
    return ["/".join(parts) + ".py"]


def _infer_patch_action(state: TaskState):
    if (state.validation.tests or {}).get("status") != "failed":
        return None
    try:
        from .proposers.slm_action_proposer import _infer_simple_patch_from_evidence

        return _infer_simple_patch_from_evidence(state)
    except Exception:
        return None


def _latest_repair_runtime_signal(state: TaskState) -> AdvisorOutput | None:
    for output in reversed(state.advisor_outputs):
        if output.name == "repair_runtime":
            return output
    return None


def _latest_repair_runtime_signal_data(state: TaskState) -> dict[str, object] | None:
    signal = _latest_repair_runtime_signal(state)
    if signal is None:
        return None
    next_action = signal.data.get("next_action")
    return {
        "label": signal.label,
        "confidence": signal.confidence,
        "source_file": signal.data.get("source_file"),
        "source_file_confidence": signal.data.get("source_file_confidence"),
        "next_action": next_action if isinstance(next_action, dict) else None,
    }


def _latest_runtime_plan_enforcement(state: TaskState) -> str | None:
    if not state.runtime_plan_audits:
        return None
    value = state.runtime_plan_audits[-1].get("enforcement")
    return str(value) if value is not None else None


def _execution_profile_summary(state: TaskState) -> dict[str, object] | None:
    profile = state.execution_profile
    if not isinstance(profile, dict):
        return None
    return {
        "profile_version": profile.get("profile_version"),
        "task_type": profile.get("task_type"),
        "strategy": profile.get("strategy"),
        "runtime": profile.get("runtime"),
        "device": profile.get("device"),
    }


def _trace_case_id(state: TaskState) -> str:
    project_name = Path(state.project_path).name
    if project_name and project_name not in {".", ""}:
        return project_name
    return state.task_id


def _previous_action(state: TaskState) -> str | None:
    if len(state.tool_history) < 2:
        return None
    return str(state.tool_history[-2].action)


def _tool_result_summary(result: ToolResult) -> dict[str, object]:
    output = result.output if isinstance(result.output, dict) else {}
    summary: dict[str, object] = {
        "action": result.action,
        "allowed": result.allowed,
        "success": result.success,
        "error": result.error,
        "policy_reason": result.policy_reason,
    }
    for key in (
        "path",
        "requested_path",
        "status",
        "exit_code",
        "applied",
        "valid",
        "approval_required",
    ):
        if key in output:
            summary[key] = output.get(key)
    return summary


def _files_touched(state: TaskState, result: ToolResult) -> list[str]:
    files: list[str] = []
    output = result.output if isinstance(result.output, dict) else {}
    for key in ("path", "requested_path"):
        value = output.get(key)
        if isinstance(value, str) and value and value not in files:
            files.append(value)
    if result.action in {"propose_patch", "apply_patch"}:
        patch = state.proposed_patch or {}
        value = patch.get("path") if isinstance(patch, dict) else None
        if isinstance(value, str) and value and value not in files:
            files.append(value)
    return files


def _event_error_category(state: TaskState, result: ToolResult) -> str | None:
    text = " ".join(
        str(value or "")
        for value in (
            result.error,
            result.policy_reason,
            result.output.get("output") if isinstance(result.output, dict) else None,
        )
    ).lower()
    if any(token in text for token in ("modulenotfounderror", "importerror", "no module named")):
        return "import_error"
    if "syntaxerror" in text or "invalid syntax" in text:
        return "syntax_error"
    if result.allowed is False:
        return "policy_block"
    if state.stop_reason:
        return str(state.stop_reason)
    return None


def _policy_blocks(state: TaskState) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for item in state.tool_history:
        if item.allowed:
            continue
        blocks.append(
            {
                "action": item.action,
                "policy_reason": item.policy_reason,
                "error": item.error,
            }
        )
    return blocks


def _final_failure_mode(state: TaskState) -> str | None:
    if state.status == "completed" and (state.validation.tests or {}).get("status") == "passed":
        return None
    if any(not item.allowed for item in state.tool_history):
        return "policy_block"
    if _repeated_read_failure(state):
        return "stale_read_loop"
    output = str((state.validation.tests or {}).get("output") or "").lower()
    if any(token in output for token in ("modulenotfounderror", "importerror", "no module named")):
        return "missing_dependency"
    if state.proposed_patch is None:
        return "no_patch_proposed"
    if _patch_applied(state) and (state.validation.tests or {}).get("status") == "failed":
        return "tests_failed_after_patch"
    if state.status == "max_steps_reached":
        return "max_steps_reached"
    return None


def _repeated_read_failure(state: TaskState) -> bool:
    reads: dict[str, int] = {}
    for result in state.tool_history:
        if result.action != "read_file":
            continue
        path = result.output.get("path")
        if isinstance(path, str):
            reads[path] = reads.get(path, 0) + 1
    return any(count >= 3 for count in reads.values())


def _write_repair_trace_events(state: TaskState, trace_dir: Path) -> None:
    if not state.repair_trace_events:
        return
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"trace_{state.task_id}.jsonl"
    with trace_path.open("a", encoding="utf-8") as handle:
        for event in state.repair_trace_events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


_GUARDED_ACTION_OVERRIDES = {"run_tests", "read_file", "analyze_ast"}


def _build_guarded_override(
    recommendation: dict,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolAction | None:
    mapped_action = str(recommendation.get("mapped_action") or "")
    if mapped_action == "run_tests":
        if not state.allow_tests:
            return None
        try:
            policy.command_args("python -m pytest -q")
        except PolicyError:
            return None
        return ToolAction(
            action="run_tests",
            reason="Advisor guarded override requested deterministic test evidence.",
            args={"command": "python -m pytest -q"},
        )

    path = str(recommendation.get("path") or "")
    if not path:
        return None

    if mapped_action == "read_file":
        if path not in state.candidate_files or path in state.inspected_files:
            return None
        try:
            policy.resolve_read_path(path)
        except (PolicyError, FileNotFoundError):
            return None
        return ToolAction(
            action="read_file",
            reason="Advisor guarded override switched to an existing candidate file.",
            args={"path": path},
        )

    if mapped_action == "analyze_ast":
        if path not in state.inspected_files or not path.endswith(".py"):
            return None
        try:
            resolved = policy.resolve_read_path(path)
        except (PolicyError, FileNotFoundError):
            return None
        if resolved.absolute.suffix.lower() != ".py":
            return None
        return ToolAction(
            action="analyze_ast",
            reason="Advisor guarded override requested AST analysis for an inspected file.",
            args={"path": path},
        )

    return None


_STDLIB_OR_EXTERNAL_MODULES = {
    "collections",
    "dataclasses",
    "datetime",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "sys",
    "typing",
}
