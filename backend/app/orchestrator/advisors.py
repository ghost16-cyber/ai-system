from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Protocol

from ..advisors.repair_advisor import RepairAdvisor
from ..advisors.repair_labels import RepairAdvisorInput
from ..benchmark.test_output_parser import parse_pytest_output
from ..specialists import SpecialistRequest, classify_error, predict_intent
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


class SpecialistIntentAdvisor:
    name = "specialist_intent"

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        prediction = predict_intent(
            SpecialistRequest(
                text=state.goal,
                context={
                    "goal": state.goal,
                    "project_path": state.project_path,
                    "existing_intent": state.intent,
                },
            )
        )
        return AdvisorOutput(
            name=self.name,
            label=prediction.label,
            confidence=prediction.confidence,
            data=prediction.model_dump(mode="json"),
            reason=(
                f"{prediction.reason} Advisory only; deterministic policy remains "
                "authoritative."
            ),
        )


class SpecialistErrorAdvisor:
    name = "specialist_error"

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        prediction = classify_error(
            SpecialistRequest(
                text=" ".join(_tool_text_outputs(state)),
                context={
                    "goal": state.goal,
                    "latest_test_output": _latest_test_output(state),
                },
            )
        )
        return AdvisorOutput(
            name=self.name,
            label=prediction.label,
            confidence=prediction.confidence,
            data=prediction.model_dump(mode="json"),
            reason=(
                f"{prediction.reason} Advisory only; deterministic policy remains "
                "authoritative."
            ),
        )


class LearnedRepairRuntimeAdvisor:
    name = "repair_runtime"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._advisor = RepairAdvisor()

    def analyze(self, state: TaskState, policy: SafetyPolicy) -> AdvisorOutput:
        input_data = _repair_advisor_input(state)
        prediction = self._advisor.predict(input_data)
        prediction_data = prediction.to_dict()
        next_action = _recommend_next_action(state, prediction_data)
        source_file = prediction_data.get("source_file") or {}
        confidence = source_file.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = prediction.overall_confidence
        return AdvisorOutput(
            name=self.name,
            label="runtime_signal" if self.enabled else "runtime_signal_disabled",
            confidence=float(confidence),
            data={
                "enabled": self.enabled,
                "available": self._advisor.available,
                "prediction": prediction_data,
                "source_file": source_file.get("value"),
                "source_file_confidence": source_file.get("confidence"),
                "next_action": next_action,
            },
            reason="Learned repair advisor runtime signal; execution remains policy-gated.",
        )


def build_default_advisors(advisor_runtime_mode: str = "off") -> list[Advisor]:
    advisors: list[Advisor] = [
        IntentRulesAdvisor(),
        SpecialistIntentAdvisor(),
        FileRelevanceAdvisor(),
        BugTypeRulesAdvisor(),
        SpecialistErrorAdvisor(),
        RiskRulesAdvisor(),
    ]
    if advisor_runtime_mode != "off":
        advisors.append(LearnedRepairRuntimeAdvisor(enabled=True))
    return advisors


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


def _repair_advisor_input(state: TaskState) -> RepairAdvisorInput:
    parsed = parse_pytest_output(_latest_test_output(state) or "")
    failing_test_file = parsed.get("failing_test_file")
    failing_test_name = parsed.get("failing_test_name")
    assertion_summary = parsed.get("assertion_summary")
    return RepairAdvisorInput(
        goal=state.goal,
        failing_test_file=failing_test_file if isinstance(failing_test_file, str) else None,
        failing_test_name=failing_test_name if isinstance(failing_test_name, str) else None,
        assertion_summary=assertion_summary if isinstance(assertion_summary, str) else None,
        imported_modules=_latest_imports(state),
        candidate_files=list(state.candidate_files),
        inspected_files=list(state.inspected_files),
        tool_actions=[str(result.action) for result in state.tool_history],
    )


def _latest_imports(state: TaskState) -> list[str]:
    for result in reversed(state.tool_history):
        if result.action == "analyze_ast" and result.success:
            imports = result.output.get("imports")
            if isinstance(imports, list):
                return [str(item) for item in imports]
    return []


def _recommend_next_action(
    state: TaskState,
    prediction: dict,
) -> dict[str, object]:
    source_file = prediction.get("source_file") or {}
    source_value = source_file.get("value") if isinstance(source_file, dict) else None
    source_confidence = source_file.get("confidence") if isinstance(source_file, dict) else None
    confidence = float(source_confidence) if isinstance(source_confidence, (int, float)) else 0.0

    if _has_repeated_read(state):
        return {
            "next_action": "stop_repeated_reads",
            "mapped_action": "analyze_ast",
            "confidence": max(confidence, 0.86),
            "reason": "Repeated read pattern detected.",
        }

    if not state.validation.tests and state.allow_tests and _goal_mentions_tests(state.goal):
        return {
            "next_action": "run_tests",
            "mapped_action": "run_tests",
            "confidence": max(confidence, 0.86),
            "reason": "Gather deterministic test evidence before repair actions.",
        }

    if isinstance(source_value, str) and source_value:
        if source_value in state.candidate_files and source_value not in state.inspected_files:
            return {
                "next_action": "switch_target_file",
                "mapped_action": "read_file",
                "path": source_value,
                "confidence": confidence,
                "reason": "Advisor source-file prediction matches an existing candidate.",
            }
        if source_value in state.inspected_files and source_value.endswith(".py"):
            return {
                "next_action": "analyze_ast",
                "mapped_action": "analyze_ast",
                "path": source_value,
                "confidence": confidence,
                "reason": "Advisor source-file prediction has been inspected.",
            }

    return {
        "next_action": "inspect_imports",
        "mapped_action": "analyze_ast",
        "confidence": max(confidence, 0.5),
        "reason": "Collect more structural context before patch decisions.",
    }


def _has_repeated_read(state: TaskState) -> bool:
    reads: dict[str, int] = {}
    for result in state.tool_history:
        if result.action != "read_file":
            continue
        path = result.output.get("path")
        if isinstance(path, str):
            reads[path] = reads.get(path, 0) + 1
    return any(count > 1 for count in reads.values())


def _goal_mentions_tests(goal: str) -> bool:
    lowered = goal.lower()
    return any(token in lowered for token in ("test", "pytest", "failing", "failure"))


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
