from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Protocol

from ..benchmark.test_output_parser import parse_pytest_output
from .models import AdvisorOutput, TaskState
from .policy import SafetyPolicy


class Advisor(Protocol):
    name: str

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        """Return one structured signal for the current task state."""


class IntentRulesAdvisor:
    name = "intent"

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        text = state.goal.lower()
        labels = {
            "debug_error": (
                "fail",
                "failing",
                "traceback",
                "error",
                "bug",
                "fix",
                "broken",
            ),
            "explain_code": ("explain", "what does", "understand", "describe"),
            "write_tests": ("test", "pytest", "coverage"),
            "refactor_code": ("refactor", "clean", "simplify"),
            "setup_project": ("setup", "install", "configure"),
            "project_question": ("where", "which file", "how is", "architecture"),
        }
        scores = {
            label: sum(1 for token in tokens if token in text)
            for label, tokens in labels.items()
        }
        label, score = max(scores.items(), key=lambda item: item[1])
        if score == 0:
            label = "project_question"
        confidence = min(0.95, 0.55 + (score * 0.12))
        return AdvisorOutput(
            name=self.name,
            label=label,
            confidence=confidence,
            reason="Rule-based keyword intent estimate.",
        )


class FileRelevanceAdvisor:
    name = "file_relevance"

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        terms = _terms(state.goal)
        priority_files = _failure_priority_files(state, policy)
        scored: list[tuple[int, str]] = []
        for path in policy.project_root.rglob("*"):
            if policy.is_ignored(path) or not path.is_file():
                continue
            if path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".toml"}:
                continue
            try:
                relative = policy.task_relative(path)
            except ValueError:
                continue
            score = _score_path(relative, terms)
            if score > 0:
                scored.append((score, relative))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top_files = _unique(priority_files + [path for _, path in scored])[:10]
        return AdvisorOutput(
            name=self.name,
            label="ranked_files" if top_files else "no_candidates",
            confidence=0.70 if top_files else 0.30,
            data={"top_files": top_files},
            reason="Ranked files by path/name keyword overlap.",
        )


class BugTypeRulesAdvisor:
    name = "bug_type"

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        text = " ".join(_tool_text_outputs(state)).lower()
        if "syntaxerror" in text or "invalid syntax" in text:
            label = "syntax_error"
        elif "importerror" in text or "modulenotfounderror" in text:
            label = "missing_import"
        elif "typeerror" in text:
            label = "type_error"
        elif "assertionerror" in text or "assert " in text:
            label = "wrong_return_value"
        elif "422" in text and ("fastapi" in text or "validation" in text):
            label = "schema_validation_error"
        elif "indexerror" in text:
            label = "index_error"
        elif "attributeerror" in text and "none" in text:
            label = "none_error"
        else:
            label = "unknown"
        return AdvisorOutput(
            name=self.name,
            label=label,
            confidence=0.80 if label != "unknown" else 0.25,
            reason="Rule-based classification from tool output text.",
        )


class RiskRulesAdvisor:
    name = "risk"

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        patch = state.proposed_patch or {}
        path = str(patch.get("path", ""))
        old = str(patch.get("old", ""))
        new = str(patch.get("new", ""))
        if not patch:
            return AdvisorOutput(
                name=self.name,
                label="unknown",
                confidence=0.20,
                data={"risk": "unknown"},
                reason="No proposed patch has been recorded yet.",
            )
        if not path.endswith(".py"):
            risk = "blocked"
            reason = "MVP only allows Python source patches."
        elif len(old.splitlines()) > 20 or len(new.splitlines()) > 20:
            risk = "high"
            reason = "Patch is larger than the MVP small-change budget."
        elif any(token in path.lower() for token in ("auth", "security", "secret")):
            risk = "high"
            reason = "Patch touches security-sensitive path naming."
        elif len(old.splitlines()) <= 5 and len(new.splitlines()) <= 5:
            risk = "low"
            reason = "Small Python source patch."
        else:
            risk = "medium"
            reason = "Moderate Python source patch."
        return AdvisorOutput(
            name=self.name,
            label=risk,
            confidence=0.75,
            data={"risk": risk},
            reason=reason,
        )


def build_default_advisors() -> list[Advisor]:
    return [
        IntentRulesAdvisor(),
        FileRelevanceAdvisor(),
        BugTypeRulesAdvisor(),
        RiskRulesAdvisor(),
    ]


def _terms(text: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "this",
        "that",
        "with",
        "from",
        "fix",
        "failing",
        "failed",
        "error",
    }
    return [
        token
        for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
        if token not in stopwords
    ]


def _score_path(path: str, terms: list[str]) -> int:
    lowered = path.lower()
    basename = Path(path).name.lower()
    score = 0
    for term in terms:
        if term in basename:
            score += 4
        elif term in lowered:
            score += 2
    if "test" in lowered:
        score += 1
    return score


def _failure_priority_files(state: TaskState, policy: SafetyPolicy) -> list[str]:
    output = _latest_test_output(state)
    if not output:
        return []
    parsed = parse_pytest_output(output)
    candidates: list[str] = []

    failing_test_file = parsed.get("failing_test_file")
    if isinstance(failing_test_file, str):
        test_path = _normalize_existing_path(failing_test_file, policy)
        if test_path:
            candidates.append(test_path)
            candidates.extend(_import_targets_for_test(policy.project_root / test_path, policy))

    for stack_path in parsed.get("stack_source_paths", []):
        if not isinstance(stack_path, str):
            continue
        normalized = _normalize_existing_path(stack_path, policy)
        if normalized:
            candidates.append(normalized)

    return _unique(candidates)


def _latest_test_output(state: TaskState) -> str | None:
    for result in reversed(state.tool_history):
        if result.action != "run_tests" or not result.success:
            continue
        output = result.output.get("output")
        return output if isinstance(output, str) else None
    return None


def _normalize_existing_path(path: str, policy: SafetyPolicy) -> str | None:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    candidate = (policy.project_root / normalized).resolve()
    try:
        candidate.relative_to(policy.project_root)
    except ValueError:
        return None
    if candidate.exists() and candidate.is_file() and not policy.is_ignored(candidate):
        return candidate.relative_to(policy.project_root).as_posix()
    name_match = policy.project_root / Path(normalized).name
    if name_match.exists() and name_match.is_file() and not policy.is_ignored(name_match):
        return name_match.relative_to(policy.project_root).as_posix()
    return None


def _import_targets_for_test(test_path: Path, policy: SafetyPolicy) -> list[str]:
    if not test_path.exists() or test_path.suffix != ".py":
        return []
    try:
        tree = ast.parse(test_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    candidates: list[str] = []
    for node in ast.walk(tree):
        module: str | None = None
        if isinstance(node, ast.ImportFrom):
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                candidates.extend(_module_to_existing_paths(alias.name, policy))
            continue
        if module:
            candidates.extend(_module_to_existing_paths(module, policy))
    return _unique(candidates)


def _module_to_existing_paths(module: str, policy: SafetyPolicy) -> list[str]:
    module_path = Path(*module.split("."))
    candidates = [
        policy.project_root / f"{module_path.as_posix()}.py",
        policy.project_root / module_path / "__init__.py",
    ]
    existing: list[str] = []
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and not policy.is_ignored(candidate):
            existing.append(candidate.relative_to(policy.project_root).as_posix())
    return existing


def _unique(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _tool_text_outputs(state: TaskState) -> list[str]:
    values: list[str] = [state.goal]
    for result in state.tool_history:
        for key in ("output", "error"):
            value = getattr(result, key)
            values.append(str(value))
    return values
