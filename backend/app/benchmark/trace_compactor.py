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
    }


def _redacted_length(value: Any) -> int | None:
    if isinstance(value, dict) and value.get("redacted"):
        length = value.get("length")
        return int(length) if isinstance(length, int) else None
    if isinstance(value, str):
        return len(value)
    return None
