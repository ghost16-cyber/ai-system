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
    requested = str(action.args.get("path", ""))
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
    requested = str(action.args.get("path", ""))
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
    requested = str(action.args.get("path", ""))
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
        "path": str(action.args.get("path", "")),
        "old": str(action.args.get("old", "")),
        "new": str(action.args.get("new", "")),
        "reason": action.reason,
    }
    resolved = policy.resolve_patch_path(patch["path"])
    scope = validate_patch_scope(patch)
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
    if not state.allow_edits:
        raise PolicyError("File edits require allow_edits=true.")
    patch = state.proposed_patch or action.args
    resolved = policy.resolve_patch_path(str(patch.get("path", "")))
    scope = validate_patch_scope(patch)
    state.validation.patch_scope = scope
    if not scope["valid"]:
        raise PolicyError(str(scope["reason"]))
    confidence = score_patch_confidence(state, resolved.relative, scope)
    state.validation.confidence = confidence
    if confidence["score"] < 0.55:
        raise PolicyError(f"Patch confidence too low to apply: {confidence['score']}")

    old = str(patch.get("old", ""))
    new = str(patch.get("new", ""))
    source = resolved.absolute.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise PolicyError("Patch old text must still occur exactly once.")
    updated = source.replace(old, new, 1)
    if resolved.absolute.suffix.lower() == ".py":
        ast.parse(updated)
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
