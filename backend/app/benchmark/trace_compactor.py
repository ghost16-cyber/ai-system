from __future__ import annotations

from typing import Any


def compact_orchestrator_trace(trace: dict[str, Any] | None) -> dict[str, Any]:
    if not trace:
        return {}

    tool_history = trace.get("tool_history") or []
    compact_tools = [_compact_tool(item) for item in tool_history if isinstance(item, dict)]

    return {
        "task_id": trace.get("task_id"),
        "goal": trace.get("goal"),
        "status": trace.get("status"),
        "intent": trace.get("intent"),
        "candidate_files": trace.get("candidate_files", [])[:10],
        "inspected_files": trace.get("inspected_files", []),
        "tool_actions": [item.get("action") for item in compact_tools],
        "tool_history": compact_tools,
        "repair_trace_events": trace.get("repair_trace_events", []),
        "advisor_action_audits": trace.get("advisor_action_audits", []),
        "runtime_plan_audits": trace.get("runtime_plan_audits", []),
        "active_runtime_plan": trace.get("active_runtime_plan"),
        "execution_profile": trace.get("execution_profile"),
        "proposed_patch": _compact_patch(trace.get("proposed_patch")),
        "validation": _compact_validation(trace.get("validation")),
        "final_response": trace.get("final_response"),
        "stop_reason": trace.get("stop_reason"),
        "step_count": trace.get("step_count"),
    }


def _compact_tool(item: dict[str, Any]) -> dict[str, Any]:
    output = item.get("output") if isinstance(item.get("output"), dict) else {}
    return {
        "action": item.get("action"),
        "allowed": item.get("allowed"),
        "success": item.get("success"),
        "error": item.get("error"),
        "policy_reason": item.get("policy_reason"),
        "path": output.get("path"),
        "status": output.get("status"),
        "exit_code": output.get("exit_code"),
        "applied": output.get("applied"),
        "message": output.get("message"),
        "requested_path": output.get("requested_path"),
        "decision": output.get("decision"),
        "blocked_signals": output.get("blocked_signals"),
        "recommended_plan": output.get("recommended_plan"),
        "runtime": output.get("runtime"),
        "device": output.get("device"),
    }


def _compact_patch(patch: Any) -> dict[str, Any] | None:
    if not isinstance(patch, dict):
        return None
    return {
        "path": patch.get("path"),
        "old_length": _redacted_length(patch.get("old")),
        "new_length": _redacted_length(patch.get("new")),
        "reason": patch.get("reason"),
    }


def _compact_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {}
    tests = validation.get("tests") if isinstance(validation.get("tests"), dict) else {}
    risk = validation.get("risk") if isinstance(validation.get("risk"), dict) else {}
    syntax = validation.get("syntax") if isinstance(validation.get("syntax"), dict) else {}
    patch_scope = (
        validation.get("patch_scope")
        if isinstance(validation.get("patch_scope"), dict)
        else {}
    )
    confidence = (
        validation.get("confidence")
        if isinstance(validation.get("confidence"), dict)
        else {}
    )
    dirty = (
        validation.get("dirty_worktree")
        if isinstance(validation.get("dirty_worktree"), dict)
        else {}
    )
    checkpoint = (
        validation.get("checkpoint")
        if isinstance(validation.get("checkpoint"), dict)
        else {}
    )
    rollback = (
        validation.get("rollback")
        if isinstance(validation.get("rollback"), dict)
        else {}
    )
    approval = (
        validation.get("approval")
        if isinstance(validation.get("approval"), dict)
        else {}
    )
    runtime_plan = (
        validation.get("runtime_plan")
        if isinstance(validation.get("runtime_plan"), dict)
        else {}
    )
    execution_profile = (
        validation.get("execution_profile")
        if isinstance(validation.get("execution_profile"), dict)
        else {}
    )
    return {
        "tests": {
            "status": tests.get("status"),
            "exit_code": tests.get("exit_code"),
            "command": tests.get("command"),
        },
        "risk": {
            "label": risk.get("label"),
            "reason": risk.get("reason"),
        },
        "syntax": {
            "valid": syntax.get("valid"),
            "path": syntax.get("path"),
        },
        "patch_scope": {
            "valid": patch_scope.get("valid"),
            "changed_line_budget": patch_scope.get("changed_line_budget"),
        },
        "confidence": {
            "score": confidence.get("score"),
            "level": confidence.get("level"),
            "decision": confidence.get("decision"),
        },
        "dirty_worktree": {
            "is_git_repo": dirty.get("is_git_repo"),
            "dirty": dirty.get("dirty"),
            "target_dirty": dirty.get("target_dirty"),
            "dirty_file_count": len(dirty.get("dirty_files") or []),
        },
        "checkpoint": {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "relative_path": checkpoint.get("relative_path"),
            "original_sha256": checkpoint.get("original_sha256"),
        },
        "rollback": {
            "restored": rollback.get("restored"),
            "relative_path": rollback.get("relative_path"),
        },
        "approval": {
            "approval_id": approval.get("approval_id"),
            "status": approval.get("status"),
        },
        "runtime_plan": {
            "allowed": runtime_plan.get("allowed"),
            "decision": runtime_plan.get("decision"),
            "reason": runtime_plan.get("reason"),
            "blocked_signals": runtime_plan.get("blocked_signals", []),
            "recommended_plan": runtime_plan.get("recommended_plan", {}),
        },
        "execution_profile": {
            "profile_version": execution_profile.get("profile_version"),
            "task_type": execution_profile.get("task_type"),
            "strategy": execution_profile.get("strategy"),
            "runtime": execution_profile.get("runtime"),
            "device": execution_profile.get("device"),
            "settings": execution_profile.get("settings", {}),
        },
    }


def _redacted_length(value: Any) -> int | None:
    if isinstance(value, dict) and value.get("redacted"):
        length = value.get("length")
        return int(length) if isinstance(length, int) else None
    if isinstance(value, str):
        return len(value)
    return None
