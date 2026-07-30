from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .approvals import save_pending_approval
from .checkpoints import create_file_checkpoint
from .git_safety import get_dirty_worktree_status
from ..local_runtime import (
    build_execution_profile as compile_execution_profile,
    build_runtime_context,
    validate_task_plan,
)
from .models import TaskState, ToolAction, ToolResult
from .policy import SafetyPolicy, PolicyError, validate_patch_scope


ToolExecutor = Callable[[ToolAction, TaskState, SafetyPolicy], ToolResult]
SEARCHABLE_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    executor: ToolExecutor


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def execute(
        self,
        action: ToolAction,
        state: TaskState,
        policy: SafetyPolicy,
    ) -> ToolResult:
        spec = self._specs.get(action.action)
        if spec is None:
            return ToolResult(
                action=action.action,
                allowed=False,
                success=False,
                error=f"Unsupported tool action: {action.action}",
            )
        try:
            return spec.executor(action, state, policy)
        except FileNotFoundError as error:
            return ToolResult(
                action=action.action,
                allowed=True,
                success=False,
                error=str(error),
                output={"requested_path": action.args.get("path")},
            )
        except (PolicyError, ValueError) as error:
            return ToolResult(
                action=action.action,
                allowed=False,
                success=False,
                error=str(error),
                policy_reason=str(error),
            )
        except Exception as error:
            return ToolResult(
                action=action.action,
                allowed=True,
                success=False,
                error=f"{type(error).__name__}: {error}",
            )


def build_default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec("search_files", "Find files in the task project.", "low", search_files),
            ToolSpec(
                "get_runtime_context",
                "Read local hardware/tool capability context for the task.",
                "low",
                get_runtime_context,
            ),
            ToolSpec(
                "validate_runtime_plan",
                "Validate an AI runtime plan against deterministic machine policy.",
                "low",
                validate_runtime_plan,
            ),
            ToolSpec(
                "build_execution_profile",
                "Compile the active validated plan into concrete runtime settings.",
                "low",
                build_execution_profile,
            ),
            ToolSpec(
                "authorize_runtime_plan",
                "Authorize only the active runtime-policy-approved plan.",
                "medium",
                authorize_runtime_plan,
            ),
            ToolSpec("read_file", "Read a policy-approved source/text file.", "low", read_file),
            ToolSpec("analyze_ast", "Extract Python AST structure.", "low", analyze_ast_file),
            ToolSpec("run_tests", "Run an allowlisted verification command.", "medium", run_tests),
            ToolSpec("validate_syntax", "Parse a Python file for syntax validity.", "low", validate_syntax),
            ToolSpec("propose_patch", "Record and validate an exact text patch.", "medium", propose_patch),
            ToolSpec("apply_patch", "Apply an approved exact text patch.", "high", apply_patch),
            ToolSpec("final_response", "Finish the task with a response.", "low", final_response),
        ]
    )


def get_slm_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_files",
            "description": "Search for files inside the task project.",
            "args": {"query": "string", "max_results": "integer | optional"},
        },
        {
            "name": "get_runtime_context",
            "description": (
                "Read compact local hardware, tool, capability, and task optimization "
                "context before choosing AI runtimes or heavy commands."
            ),
            "args": {"task": "string | optional"},
        },
        {
            "name": "validate_runtime_plan",
            "description": (
                "Required policy gate for proposed AI model, training, RAG, or "
                "GPU execution plans. Obey its recommended_plan on downgrade."
            ),
            "args": {
                "task": "string",
                "requested_plan": "object",
            },
        },
        {
            "name": "build_execution_profile",
            "description": (
                "Compile the active approved or downgraded plan into concrete "
                "runtime, device, tool, timeout, memory, and workload settings."
            ),
            "args": {"task": "string | optional"},
        },
        {
            "name": "authorize_runtime_plan",
            "description": (
                "Required immediately before future AI workload execution. "
                "Fails unless validate_runtime_plan approved the same active plan."
            ),
            "args": {"plan": "object | optional"},
        },
        {
            "name": "read_file",
            "description": "Read an allowed file inside the task project.",
            "args": {"path": "string"},
        },
        {
            "name": "analyze_ast",
            "description": "Analyze Python AST structure for a file.",
            "args": {"path": "string"},
        },
        {
            "name": "run_tests",
            "description": "Run an approved test command.",
            "args": {"command": "string"},
        },
        {
            "name": "validate_syntax",
            "description": "Validate Python syntax for a file.",
            "args": {"path": "string"},
        },
        {
            "name": "propose_patch",
            "description": "Propose a small old/new text replacement patch.",
            "args": {
                "path": "string",
                "old": "string",
                "new": "string",
            },
        },
        {
            "name": "apply_patch",
            "description": "Apply the currently validated proposed patch.",
            "args": {},
        },
        {
            "name": "final_response",
            "description": "Return the final answer to the user.",
            "args": {"message": "string"},
        },
    ]


def get_runtime_context(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    task = str(action.args.get("task") or state.goal)
    context = build_runtime_context(
        task=task,
        workspace_root=policy.project_root,
    )
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={
            "slm_context": context.slm_context,
            "policy": context.policy.model_dump(mode="json"),
            "task_optimization": context.task_optimization.model_dump(mode="json"),
            "capabilities": [
                capability.model_dump(mode="json")
                for capability in context.capabilities
            ],
            "tool_availability": {
                tool.name: tool.available
                for tool in context.tools
            },
        },
    )


def validate_runtime_plan(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    task = str(action.args.get("task") or state.goal)
    requested_plan = action.args.get("requested_plan")
    if not isinstance(requested_plan, dict):
        raise ValueError("requested_plan must be an object.")

    context = build_runtime_context(
        task=task,
        workspace_root=policy.project_root,
    )
    validation = validate_task_plan(
        task=task,
        requested_plan=requested_plan,
        runtime_context=context,
    )
    output = validation.model_dump(mode="json")
    state.validation.runtime_plan = output
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output=output,
    )


def authorize_runtime_plan(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    validation = state.validation.runtime_plan
    if not isinstance(validation, dict):
        raise PolicyError(
            "validate_runtime_plan must run before runtime plan authorization."
        )
    if validation.get("decision") == "block":
        raise PolicyError("Blocked runtime plans cannot be authorized.")
    if not isinstance(state.active_runtime_plan, dict):
        raise PolicyError("No active approved runtime plan is available.")
    if not isinstance(state.execution_profile, dict):
        raise PolicyError(
            "build_execution_profile must run before runtime plan authorization."
        )
    if state.execution_profile.get("source_plan") != state.active_runtime_plan:
        raise PolicyError("Execution profile does not match the active runtime plan.")

    requested = action.args.get("plan", state.active_runtime_plan)
    if not isinstance(requested, dict):
        raise ValueError("plan must be an object.")
    if requested != state.active_runtime_plan:
        raise PolicyError(
            "Runtime plan does not match the active approved or downgraded plan."
        )

    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={
            "authorized": True,
            "decision": validation.get("decision"),
            "active_runtime_plan": state.active_runtime_plan,
        },
    )


def build_execution_profile(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    validation = state.validation.runtime_plan
    if not isinstance(validation, dict):
        raise PolicyError(
            "validate_runtime_plan must run before building an execution profile."
        )
    if validation.get("decision") == "block":
        raise PolicyError("Blocked runtime plans cannot produce execution profiles.")
    if not isinstance(state.active_runtime_plan, dict):
        raise PolicyError("No active validated runtime plan is available.")

    task = str(action.args.get("task") or state.goal)
    context = build_runtime_context(
        task=task,
        workspace_root=policy.project_root,
    )
    profile = compile_execution_profile(
        task=task,
        runtime_context=context,
        active_runtime_plan=state.active_runtime_plan,
    )
    output = profile.model_dump(mode="json")
    state.execution_profile = output
    state.validation.execution_profile = output
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output=output,
    )


def search_files(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    query = str(action.args.get("query", state.goal))
    max_results = int(action.args.get("max_results", 20))
    terms = [token.lower() for token in query.replace("_", " ").split() if len(token) >= 3]
    matches: list[dict[str, Any]] = []

    for path in policy.project_root.rglob("*"):
        if policy.is_ignored(path) or not path.is_file():
            continue
        if path.suffix.lower() not in SEARCHABLE_SUFFIXES:
            continue
        relative = policy.task_relative(path)
        lowered = relative.lower()
        score = sum(1 for term in terms if term in lowered)
        if score == 0 and terms:
            continue
        matches.append(
            {
                "path": relative,
                "name": path.name,
                "extension": path.suffix.lower() or "[no extension]",
                "score": score,
            }
        )

    matches.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    matches = _include_test_counterparts(matches, policy)
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={"query": query, "matches": matches[:max_results]},
    )


def read_file(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    requested = str(action.args.get("path", "")).strip()
    max_bytes = int(action.args.get("max_bytes", 50_000))
    resolved = policy.resolve_read_path(requested)
    if resolved.absolute.stat().st_size > max_bytes:
        raise PolicyError("File exceeds read size limit for this task.")
    try:
        content = resolved.absolute.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("File must be UTF-8 encoded.") from error

    if resolved.relative not in state.inspected_files:
        state.inspected_files.append(resolved.relative)

    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={
            "path": resolved.relative,
            "content": content,
            "line_count": len(content.splitlines()),
        },
    )


def analyze_ast_file(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    requested = str(action.args.get("path", "")).strip()
    resolved = policy.resolve_read_path(requested)
    if resolved.absolute.suffix.lower() != ".py":
        raise PolicyError("AST analysis currently supports Python files only.")
    source = resolved.absolute.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: list[str] = []
    routes: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = [_decorator_name(item) for item in node.decorator_list]
            functions.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "async": isinstance(node, ast.AsyncFunctionDef),
                    "args": [arg.arg for arg in node.args.args],
                    "decorators": [item for item in decorators if item],
                }
            )
            for decorator in decorators:
                if decorator and any(
                    part in decorator for part in (".get", ".post", ".put", ".delete", ".patch")
                ):
                    routes.append({"function": node.name, "decorator": decorator})
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno})
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={
            "path": resolved.relative,
            "functions": functions,
            "classes": classes,
            "imports": sorted(set(imports)),
            "routes": routes,
        },
    )


def run_tests(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    if not state.allow_tests:
        raise PolicyError("Test execution is disabled for this task.")
    command = str(action.args.get("command", "python -m pytest -q"))
    args = policy.command_args(command)
    if args and args[0] == "python":
        args = [sys.executable] + args[1:]
    _clear_pycache(policy.project_root)
    completed = subprocess.run(
        args,
        cwd=policy.project_root,
        capture_output=True,
        env=_test_env(),
        text=True,
        timeout=int(action.args.get("timeout_seconds", 60)),
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    if len(output) > 4000:
        output = output[-4000:]
    result = {
        "command": command,
        "exit_code": completed.returncode,
        "status": "passed" if completed.returncode == 0 else "failed",
        "output": output,
    }
    state.validation.tests = result
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output=result,
    )


def _clear_pycache(root: Path) -> None:
    for path in root.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _test_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TMP"] = "/tmp"
    env["TEMP"] = "/tmp"
    env["TMPDIR"] = "/tmp"
    return env


def validate_syntax(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    requested = str(action.args.get("path", "")).strip()
    resolved = policy.resolve_read_path(requested)
    if resolved.absolute.suffix.lower() != ".py":
        raise PolicyError("Syntax validation currently supports Python files only.")
    source = resolved.absolute.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as error:
        result = {
            "path": resolved.relative,
            "valid": False,
            "error": f"{error.__class__.__name__}: {error}",
        }
    else:
        result = {"path": resolved.relative, "valid": True}
    state.validation.syntax = result
    return ToolResult(action=action.action, allowed=True, success=True, output=result)


def propose_patch(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    patch = {
        "path": str(action.args.get("path", "")).strip(),
        "old": str(action.args.get("old", "")),
        "new": str(action.args.get("new", "")),
        "reason": action.reason,
    }
    resolved = policy.resolve_patch_path(patch["path"])
    _ensure_patch_file_allowed(state, resolved.relative)
    scope = validate_patch_scope(patch, max_changed_lines=state.max_patch_changed_lines)
    state.validation.patch_scope = scope
    if not scope["valid"]:
        raise PolicyError(str(scope["reason"]))
    source = resolved.absolute.read_text(encoding="utf-8")
    occurrences = source.count(patch["old"])
    if occurrences != 1:
        raise PolicyError(
            f"Patch old text must occur exactly once; found {occurrences} occurrence(s)."
        )
    state.proposed_patch = {**patch, "path": resolved.relative}
    confidence = score_patch_confidence(state, resolved.relative, scope)
    state.validation.confidence = confidence
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={
            "proposed_patch": _redact_patch(state.proposed_patch),
            "scope": scope,
            "confidence": confidence,
        },
    )


def apply_patch(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    if state.approval_mode == "never" or not state.allow_edits:
        raise PolicyError("File edits require allow_edits=true.")
    patch = state.proposed_patch or action.args
    resolved = policy.resolve_patch_path(str(patch.get("path", "")).strip())
    _ensure_patch_file_allowed(state, resolved.relative)
    scope = validate_patch_scope(patch, max_changed_lines=state.max_patch_changed_lines)
    state.validation.patch_scope = scope
    if not scope["valid"]:
        raise PolicyError(str(scope["reason"]))
    confidence = score_patch_confidence(state, resolved.relative, scope)
    state.validation.confidence = confidence
    if confidence["score"] < 0.55:
        raise PolicyError(f"Patch confidence too low to apply: {confidence['score']}")

    dirty = get_dirty_worktree_status(policy.project_root, resolved.absolute)
    state.validation.dirty_worktree = dirty.to_dict()
    if dirty.dirty and not state.allow_dirty_worktree:
        raise PolicyError(
            "Dirty working tree detected; refusing to apply patch without explicit override."
        )

    old = str(patch.get("old", ""))
    new = str(patch.get("new", ""))
    source = resolved.absolute.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise PolicyError("Patch old text must still occur exactly once.")
    updated = source.replace(old, new, 1)
    if resolved.absolute.suffix.lower() == ".py":
        ast.parse(updated)

    if state.approval_mode == "review":
        approval = save_pending_approval(
            approval_root=state.approval_root,
            task_id=state.task_id,
            workspace_root=Path(state.workspace),
            project_path=state.project_path,
            patch={**patch, "path": resolved.relative},
            confidence=confidence,
            allow_tests=state.allow_tests,
            allow_dirty_worktree=state.allow_dirty_worktree,
            rollback_on_test_failure=state.rollback_on_test_failure,
            checkpoint_root=state.checkpoint_root,
        )
        state.validation.approval = approval
        state.status = "needs_approval"
        state.final_response = (
            "Patch is ready for human review. Approve it with "
            f"approval_id={approval['approval_id']}."
        )
        return ToolResult(
            action=action.action,
            allowed=True,
            success=True,
            output={
                "path": resolved.relative,
                "applied": False,
                "approval_required": True,
                "approval": approval,
                "scope": scope,
                "confidence": confidence,
                "dirty_worktree": dirty.to_dict(),
            },
        )

    checkpoint = create_file_checkpoint(
        checkpoint_root=state.checkpoint_root,
        task_id=state.task_id,
        project_root=policy.project_root,
        relative_path=resolved.relative,
        patch={**patch, "path": resolved.relative},
    )
    state.validation.checkpoint = checkpoint
    resolved.absolute.write_text(updated, encoding="utf-8")
    state.validation.syntax = {"path": resolved.relative, "valid": True}
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={
            "path": resolved.relative,
            "applied": True,
            "scope": scope,
            "confidence": confidence,
            "checkpoint": checkpoint,
            "dirty_worktree": dirty.to_dict(),
        },
    )


def score_patch_confidence(
    state: TaskState,
    patch_path: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    score = 0.25
    reasons: list[str] = ["base exact-text patch confidence"]

    if (state.validation.tests or {}).get("status") == "failed":
        score += 0.20
        reasons.append("failing tests were observed")
    if patch_path in state.inspected_files:
        score += 0.15
        reasons.append("target file was inspected")
    if _was_ast_analyzed(state, patch_path):
        score += 0.10
        reasons.append("target AST was analyzed")
    if scope.get("valid") is True:
        score += 0.10
        reasons.append("patch scope is valid")
    if int(scope.get("changed_line_budget") or 99) <= 5:
        score += 0.10
        reasons.append("patch is small")
    if patch_path.endswith(".py") and not _is_test_path(patch_path):
        score += 0.10
        reasons.append("patch targets Python source, not tests")
    if (state.validation.syntax or {}).get("valid") is True:
        score += 0.05
        reasons.append("syntax validation exists")

    if _is_test_path(patch_path):
        score -= 0.30
        reasons.append("patch targets a test file")

    score = round(max(0.0, min(score, 1.0)), 3)
    if score >= 0.80:
        decision = "apply_allowed"
    elif score >= 0.55:
        decision = "apply_with_verification"
    else:
        decision = "fallback"
    return {
        "score": score,
        "level": _confidence_level(score),
        "decision": decision,
        "reasons": reasons,
    }


def _ensure_patch_file_allowed(state: TaskState, relative_path: str) -> None:
    allowed = {path for path in state.allowed_patch_files if path}
    if allowed and relative_path not in allowed:
        raise PolicyError(
            "Patch target is outside the allowed real-repo source files: "
            f"{relative_path}"
        )


def _was_ast_analyzed(state: TaskState, path: str) -> bool:
    return any(
        result.action == "analyze_ast"
        and result.success
        and result.output.get("path") == path
        for result in state.tool_history
    )


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py")


def _confidence_level(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.55:
        return "medium"
    if score > 0:
        return "low"
    return "unsafe"


def final_response(
    action: ToolAction,
    state: TaskState,
    policy: SafetyPolicy,
) -> ToolResult:
    message = str(action.args.get("message", "")).strip()
    if not message:
        message = "Task stopped without a final message from the proposer."
    state.final_response = message
    state.status = "completed"
    return ToolResult(
        action=action.action,
        allowed=True,
        success=True,
        output={"message": message},
    )


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _redact_patch(patch: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": patch.get("path"),
        "old_length": len(str(patch.get("old", ""))),
        "new_length": len(str(patch.get("new", ""))),
        "reason": patch.get("reason"),
    }


def _include_test_counterparts(
    matches: list[dict[str, Any]],
    policy: SafetyPolicy,
) -> list[dict[str, Any]]:
    seen = {str(match["path"]) for match in matches}
    expanded = list(matches)
    for match in matches:
        path = Path(str(match["path"]))
        name = path.name
        candidates: list[Path] = []
        if name.startswith("test_") and name.endswith(".py"):
            candidates.append(path.with_name(name.removeprefix("test_")))
        if name.endswith("_test.py"):
            candidates.append(path.with_name(name.removesuffix("_test.py") + ".py"))
        for candidate in candidates:
            absolute = policy.project_root / candidate
            candidate_text = candidate.as_posix()
            if candidate_text in seen or not absolute.exists() or policy.is_ignored(absolute):
                continue
            expanded.append(
                {
                    "path": candidate_text,
                    "name": candidate.name,
                    "extension": candidate.suffix.lower() or "[no extension]",
                    "score": max(int(match["score"]) - 1, 0),
                }
            )
            seen.add(candidate_text)
    expanded.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return expanded
