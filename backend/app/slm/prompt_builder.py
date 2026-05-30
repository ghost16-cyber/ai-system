from __future__ import annotations

import json
from typing import Any


def compact_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def summarize_state_for_slm(state: Any) -> dict[str, Any]:
    """
    Keep this summary small. Do not dump huge file contents into the SLM prompt.
    """

    return {
        "task_id": getattr(state, "task_id", None),
        "goal": getattr(state, "goal", None),
        "intent": getattr(state, "intent", None),
        "status": getattr(state, "status", None),
        "candidate_files": getattr(state, "candidate_files", [])[:5],
        "inspected_files": getattr(state, "inspected_files", [])[-5:],
        "inspected_file_snippets": _recent_file_snippets(
            getattr(state, "tool_history", [])
        ),
        "advisor_outputs": _latest_advisor_outputs(
            getattr(state, "advisor_outputs", [])
        ),
        "validation": _model_dump(getattr(state, "validation", {})),
        "proposed_patch": _summarize_patch(getattr(state, "proposed_patch", None)),
        "evidence": _summarize_evidence(getattr(state, "evidence", [])),
        "tool_history": _summarize_tool_history(getattr(state, "tool_history", [])),
    }


def _summarize_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summarized = []

    for item in evidence[-4:]:
        summarized.append(
            {
                "action": item.get("action") or item.get("type"),
                "summary": _summarize_output(item.get("output", item)),
            }
        )

    return summarized


def _summarize_tool_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summarized = []

    for item in history[-5:]:
        item = _model_dump(item)
        summarized.append(
            {
                "tool": item.get("tool") or item.get("action"),
                "allowed": item.get("allowed"),
                "success": item.get("success"),
                "error": item.get("error"),
                "summary": item.get("summary")
                or _summarize_output(item.get("output", item)),
            }
        )

    return summarized


def _recent_file_snippets(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snippets: dict[str, dict[str, Any]] = {}
    for item in history:
        item = _model_dump(item)
        if not isinstance(item, dict) or item.get("action") != "read_file":
            continue
        output = item.get("output", {})
        if not isinstance(output, dict):
            continue
        path = output.get("path")
        content = output.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        snippets[path] = {
            "path": path,
            "line_count": output.get("line_count"),
            "content_snippet": content[:1200],
            "truncated": len(content) > 1200,
        }
    return list(snippets.values())[-3:]


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_dump(item) for item in value]
    return value


def _latest_advisor_outputs(outputs: Any) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for output in _model_dump(outputs):
        if isinstance(output, dict) and output.get("name"):
            latest[str(output["name"])] = {
                "name": output.get("name"),
                "label": output.get("label"),
                "confidence": output.get("confidence"),
                "data": _compact_data(output.get("data", {})),
                "reason": output.get("reason"),
            }
    return list(latest.values())


def _summarize_patch(patch: Any) -> dict[str, Any] | None:
    if not patch:
        return None
    patch = _model_dump(patch)
    if not isinstance(patch, dict):
        return None
    return {
        "path": patch.get("path"),
        "old_length": len(str(patch.get("old", ""))),
        "new_length": len(str(patch.get("new", ""))),
        "reason": patch.get("reason"),
    }


def _summarize_output(output: Any) -> str:
    output = _model_dump(output)
    if isinstance(output, dict):
        if "content" in output:
            return (
                f"read {output.get('path')} "
                f"({output.get('line_count')} lines, content omitted)"
            )
        if "matches" in output:
            paths = [
                str(item.get("path"))
                for item in output.get("matches", [])[:5]
                if isinstance(item, dict)
            ]
            return f"matches: {paths}"
        if "functions" in output or "classes" in output:
            return (
                f"AST {output.get('path')}: "
                f"functions={_names(output.get('functions', []))[:8]}, "
                f"classes={_names(output.get('classes', []))[:8]}"
            )
        if "exit_code" in output:
            text = str(output.get("output", ""))
            return (
                f"tests {output.get('status')} exit={output.get('exit_code')} "
                f"output_tail={text[-600:]}"
            )
        if "proposed_patch" in output:
            return f"patch proposed: {output.get('proposed_patch')}"
        if "message" in output:
            return str(output["message"])[:600]
    return str(output)[:600]


def _compact_data(data: Any) -> Any:
    data = _model_dump(data)
    if isinstance(data, dict) and "top_files" in data:
        return {**data, "top_files": data.get("top_files", [])[:5]}
    return data


def _names(items: Any) -> list[str]:
    names: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        else:
            names.append(str(item))
    return names


def build_action_prompt(state: Any, available_tools: list[dict[str, Any]]) -> str:
    state_summary = summarize_state_for_slm(state)

    return f"""
You are the action proposer inside a controlled coding assistant.

You do not execute tools.
You only choose the next tool action.

The Python orchestrator will decide whether your action is allowed.
The tools and validators establish truth.

Important rules:
- Return JSON only.
- Do not use markdown.
- Do not explain outside JSON.
- Use only one action at a time.
- Use only available tool names.
- Do not invent files.
- Do not claim tests passed unless validation/tests say they passed.
- Prefer gathering evidence before editing.
- Prefer run_tests before fixing a failing-test task.
- Prefer read_file/analyze_ast before proposing a patch.
- Never call read_file for a path already listed in inspected_files.
- Use inspected_file_snippets as the current file content; do not reread those files.
- When tests failed and snippets show a small exact bug, choose propose_patch with exact old/new text.
- If you cannot make progress, choose final_response instead of repeating a previous action.
- Use final_response only when enough evidence exists or the task cannot continue safely.

Available tools:
{compact_json(available_tools)}

Current task state:
{compact_json(state_summary)}

Return exactly this JSON shape:
{{
  "action": "tool_name",
  "reason": "short reason",
  "args": {{}}
}}

Example:
If inspected snippets show calculator.py has `return a - b` and the test expects
`add(2, 3) == 5`, choose:
{{
  "action": "propose_patch",
  "reason": "add subtracts instead of adding",
  "args": {{
    "path": "calculator.py",
    "old": "return a - b",
    "new": "return a + b"
  }}
}}

Choose the next safest useful action now.
""".strip()
