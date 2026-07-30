from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.folders.safety import resolve_project_path, safe_relative_path
from backend.app.project_analysis.state_manifest import (
    IncompleteProjectManifestError, ProjectStateManifest, build_project_state_manifest,
)
from backend.app.project_analysis.symbols import analyze_source
from backend.app.project_delivery.contracts import (
    ExecutionPlanRevision, PerformedCheck, TaskSpecification, VerificationEvidence,
    VerificationMode, VerifierOutcome, VerifierResult, WorkUnitExecutionState,
)


VERIFIER_VERSION = "astra-deterministic-verifier/1"


class ProjectVerifierError(ValueError):
    def __init__(self, message: str, *, code: str = "missing_checker") -> None:
        super().__init__(message)
        self.code = code


def run_deterministic_verifier(
    job: dict[str, Any], *, root: str | Path, criterion_id: str,
    command_result: dict[str, Any] | None = None,
) -> VerifierResult:
    revision = ExecutionPlanRevision.model_validate(job.get("plan_revision"))
    specification = TaskSpecification.model_validate(job.get("specification"))
    criterion = next((item for item in specification.acceptance_criteria if item.criterion_id == criterion_id), None)
    if criterion is None:
        raise ProjectVerifierError("Acceptance criterion not found.", code="unknown_criterion")
    states = [WorkUnitExecutionState.model_validate(item) for item in job.get("work_unit_execution_states") or []]
    unit_id = str(job.get("active_work_unit_id") or "") or next(
        (unit.work_unit_id for unit in revision.work_units if criterion_id in unit.acceptance_criteria_ids), None
    )
    unit = next((item for item in revision.work_units if item.work_unit_id == unit_id), None)
    if unit is None:
        raise ProjectVerifierError("No immutable work unit maps to this criterion.", code="missing_checker")
    if not any(state.work_unit_id == unit.work_unit_id and state.plan_revision_id == revision.plan_revision_id for state in states):
        raise ProjectVerifierError("The verifier cannot bind to current work-unit execution state.", code="stale_verifier_result")
    manifest = build_project_state_manifest(root, workspace_id=str(job.get("folder_access_id") or ""))
    entries = {item.normalized_relative_path: item for item in manifest.entries}
    checks: list[PerformedCheck] = []
    evidence: list[VerificationEvidence] = []
    mode = criterion.verification_mode

    if mode == VerificationMode.MANUAL:
        outcome = VerifierOutcome.MANUAL_REQUIRED
        summary = "This criterion requires human observation and cannot be passed automatically."
    elif mode in {
        VerificationMode.EXISTING_TEST, VerificationMode.NEW_TEST, VerificationMode.TYPE_CHECK,
        VerificationMode.LINT, VerificationMode.BUILD, VerificationMode.APPROVED_COMMAND,
    }:
        checks, evidence = _check_command(command_result, manifest, mode)
        outcome = _aggregate(checks)
        summary = _summary(outcome, checks)
    elif mode == VerificationMode.FILE_PRESENCE:
        paths = [criterion.checker_rule.relative_path] if criterion.checker_rule and criterion.checker_rule.relative_path else list(unit.expected_files)
        checks, evidence = _check_files(paths, entries, manifest)
        outcome = _aggregate(checks)
        summary = _summary(outcome, checks)
    elif mode == VerificationMode.STRUCTURAL:
        checks, evidence = _check_structure(root, unit.expected_files, unit.expected_symbols, entries, manifest, criterion.checker_rule)
        outcome = _aggregate(checks)
        summary = _summary(outcome, checks)
    elif mode == VerificationMode.CONFIGURATION:
        checks, evidence = _check_configuration(root, unit.expected_files, entries, manifest, criterion.checker_rule)
        outcome = _aggregate(checks)
        summary = _summary(outcome, checks)
    elif mode == VerificationMode.EXACT_ASSERTION:
        checks, evidence = _check_exact(job, root, unit.expected_files, entries, manifest, criterion.checker_rule)
        outcome = _aggregate(checks)
        summary = _summary(outcome, checks)
    else:
        raise ProjectVerifierError(f"No deterministic checker exists for {mode.value}.", code="missing_checker")

    now = datetime.now(timezone.utc)
    data = {
        "verifier_result_id": uuid4().hex,
        "project_run_id": revision.project_run_id,
        "plan_revision_id": revision.plan_revision_id,
        "work_unit_id": unit.work_unit_id,
        "criterion_id": criterion_id,
        "criterion_definition_hash": _canonical_hash(criterion.model_dump(mode="json")),
        "verifier_type": mode.value,
        "verifier_version": VERIFIER_VERSION,
        "attempt_id": uuid4().hex,
        "input_manifest_hash": manifest.manifest_hash,
        "checked_at": now,
        "performed_checks": tuple(checks),
        "evidence_items": tuple(evidence),
        "outcome": outcome,
        "summary": summary,
        "failure_reason": None if outcome == VerifierOutcome.PASSED else summary,
    }
    unsigned = VerifierResult(**data, result_hash="0" * 64)
    payload = unsigned.model_dump(mode="json")
    payload.pop("result_hash", None)
    return unsigned.model_copy(update={"result_hash": _canonical_hash(payload)})


def assert_verifier_result_fresh(
    result: VerifierResult | dict[str, Any], job: dict[str, Any], *, root: str | Path,
) -> VerifierResult:
    value = result if isinstance(result, VerifierResult) else VerifierResult.model_validate(result)
    revision = ExecutionPlanRevision.model_validate(job.get("plan_revision"))
    specification = TaskSpecification.model_validate(job.get("specification"))
    criterion = next((item for item in specification.acceptance_criteria if item.criterion_id == value.criterion_id), None)
    if criterion is None or value.criterion_definition_hash != _canonical_hash(criterion.model_dump(mode="json")):
        raise ProjectVerifierError("The acceptance criterion changed after verification.", code="stale_verifier_result")
    if value.plan_revision_id != revision.plan_revision_id:
        raise ProjectVerifierError("The verifier result belongs to a superseded plan revision.", code="stale_verifier_result")
    current = build_project_state_manifest(root, workspace_id=str(job.get("folder_access_id") or ""))
    if value.input_manifest_hash != current.manifest_hash:
        raise ProjectVerifierError("The project changed after verification.", code="stale_verifier_result")
    payload = value.model_dump(mode="json")
    payload.pop("result_hash", None)
    expected_hash = _canonical_hash(payload)
    if value.result_hash != expected_hash:
        raise ProjectVerifierError("The verifier result hash is invalid.", code="stale_verifier_result")
    return value


def _check_files(paths, entries, manifest):
    checks: list[PerformedCheck] = []
    evidence: list[VerificationEvidence] = []
    for raw in dict.fromkeys(str(item) for item in paths if item):
        path = safe_relative_path(raw)
        entry = entries.get(path)
        passed = entry is not None
        checks.append(PerformedCheck(
            checker="manifest.file_exists", checker_version=VERIFIER_VERSION, inputs=(path,),
            condition=f"The required file {path} exists in the fresh complete manifest.",
            observed_value="present" if passed else "missing", expected_value_or_rule="present",
            outcome="passed" if passed else "failed",
        ))
        if entry:
            evidence.append(_entry_evidence(entry, manifest, "file_presence", "File is present in the complete manifest."))
    if not checks:
        checks.append(_inconclusive("manifest.file_exists", "No required path was declared."))
    return checks, evidence


def _check_structure(root, paths, symbols, entries, manifest, rule):
    checks, evidence = _check_files(paths, entries, manifest)
    if any(item.outcome == "failed" for item in checks):
        return checks, evidence
    symbol_names = list(symbols)
    if rule and rule.rule_type == "python_symbol" and rule.selector:
        symbol_names = [rule.selector]
        paths = [rule.relative_path] if rule.relative_path else paths
    for raw in paths:
        path = safe_relative_path(raw)
        candidate = resolve_project_path(root, path)
        if candidate.suffix.lower() == ".py":
            try:
                tree = ast.parse(candidate.read_text(encoding="utf-8", errors="strict"), filename=path)
                available = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                relevant = [_symbol_name(name) for name in symbol_names if _symbol_name(name)]
                missing = sorted(set(relevant) - available) if relevant else []
                checks.append(PerformedCheck(
                    checker="python.ast_structure", checker_version=VERIFIER_VERSION, inputs=(path,),
                    condition="Python parses and expected symbols are present.",
                    observed_value=f"symbols={sorted(available)[:50]}; missing={missing}",
                    expected_value_or_rule=f"parseable; symbols={relevant or 'not specified'}",
                    outcome="failed" if missing else "passed",
                ))
            except (OSError, UnicodeError, SyntaxError) as error:
                checks.append(PerformedCheck(
                    checker="python.ast_structure", checker_version=VERIFIER_VERSION, inputs=(path,),
                    condition="Python source parses.", observed_value=type(error).__name__,
                    expected_value_or_rule="valid Python AST", outcome="failed",
                ))
        elif candidate.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            text = candidate.read_text(encoding="utf-8", errors="replace")[:512_000]
            relevant = [_symbol_name(name) for name in symbol_names if _symbol_name(name)]
            language = {".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx"}[candidate.suffix.lower()]
            analysis = analyze_source(path, language, text)
            available = {str(item.get("name") or "").split(".")[-1] for item in analysis.get("symbols") or []}
            missing = [name for name in relevant if name not in available]
            parse_status = str(analysis.get("parse_status") or "partial")
            outcome = "failed" if parse_status == "failed" or missing else "passed" if parse_status == "complete" else "inconclusive"
            checks.append(PerformedCheck(
                checker="javascript.structural_analysis", checker_version=VERIFIER_VERSION, inputs=(path,),
                condition="The existing bounded JavaScript/TypeScript analyzer parses source and reports expected symbols.",
                observed_value=f"parse_status={parse_status}; parser={analysis.get('parser')}; missing={missing}",
                expected_value_or_rule=f"complete parse; symbols={relevant or 'file-readable'}",
                outcome=outcome,
            ))
    return checks, evidence


def _check_configuration(root, paths, entries, manifest, rule):
    candidates = [rule.relative_path] if rule and rule.relative_path else [
        path for path in paths if Path(path).suffix.lower() in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
    ]
    checks: list[PerformedCheck] = []
    evidence: list[VerificationEvidence] = []
    for raw in candidates:
        if not raw:
            continue
        path = safe_relative_path(raw)
        entry = entries.get(path)
        if entry is None:
            checks.append(_failed("configuration.value", path, "missing", "configuration file present"))
            continue
        candidate = resolve_project_path(root, path)
        try:
            parsed = _parse_configuration(candidate)
            observed = _select(parsed, rule.selector) if rule and rule.selector else parsed
            expected = rule.expected_value if rule else None
            passed = expected is None or observed == expected
            checks.append(PerformedCheck(
                checker="configuration.typed_value", checker_version=VERIFIER_VERSION, inputs=(path,),
                condition=f"Configuration parses{f' and {rule.selector} matches' if rule and rule.selector else ''}.",
                observed_value=_bounded_json(observed), expected_value_or_rule=_bounded_json(expected) if expected is not None else "valid parse",
                outcome="passed" if passed else "failed",
            ))
            evidence.append(_entry_evidence(entry, manifest, "configuration", "Configuration was parsed and checked."))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            checks.append(_failed("configuration.typed_value", path, type(error).__name__, "valid configuration and expected value"))
    if not checks:
        checks.append(_inconclusive("configuration.typed_value", "No configuration path or typed rule was declared."))
    return checks, evidence


def _check_exact(job, root, paths, entries, manifest, rule):
    if rule and rule.rule_type == "text_contains" and rule.relative_path and rule.selector is not None:
        path = safe_relative_path(rule.relative_path)
        entry = entries.get(path)
        if entry is None:
            return [_failed("text.contains", path, "missing", rule.selector)], []
        text = resolve_project_path(root, path).read_text(encoding="utf-8", errors="replace")[:512_000]
        passed = rule.selector in text
        return [PerformedCheck(
            checker="text.contains", checker_version=VERIFIER_VERSION, inputs=(path,),
            condition="Bounded text contains the exact required value.", observed_value="present" if passed else "absent",
            expected_value_or_rule=rule.selector, outcome="passed" if passed else "failed",
        )], [_entry_evidence(entry, manifest, "text_assertion", "Bounded exact text assertion evaluated.")]
    actual = {safe_relative_path(path) for ref in job.get("patch_references") or [] if ref.get("status") == "applied" for path in ref.get("file_set") or []}
    expected = {safe_relative_path(path) for path in paths}
    outside = sorted(actual - expected)
    return [PerformedCheck(
        checker="patch.scope_assertion", checker_version=VERIFIER_VERSION, inputs=tuple(sorted(actual)),
        condition="Applied patch paths remain inside the immutable work-unit file set.",
        observed_value=f"outside_scope={outside}", expected_value_or_rule=f"allowed={sorted(expected)}",
        outcome="failed" if outside else "passed",
    )], []


def _check_command(result, manifest, mode):
    if not isinstance(result, dict) or not result.get("execution_id"):
        return [_inconclusive("approved_command.result", "No approved command execution result was supplied.")], []
    succeeded = bool(result.get("succeeded"))
    digest = hashlib.sha256(json.dumps({
        "execution_id": result.get("execution_id"), "return_code": result.get("return_code"),
        "succeeded": succeeded, "finished_at": result.get("finished_at"),
    }, sort_keys=True, default=str).encode()).hexdigest()
    check = PerformedCheck(
        checker="approved_command.result", checker_version=VERIFIER_VERSION,
        inputs=(str(result["execution_id"]),), condition=f"Approved {mode.value} command completed successfully.",
        observed_value=f"succeeded={succeeded}; return_code={result.get('return_code')}",
        expected_value_or_rule="succeeded=true and return_code=0", outcome="passed" if succeeded else "failed",
    )
    item = VerificationEvidence(
        evidence_type="approved_command", source_identifier=str(result["execution_id"]),
        relevant_file_or_artifact=None, content_hash=digest,
        observation="Bounded approved command outcome checked.",
        provenance=(manifest.manifest_hash,), observed_at=datetime.now(timezone.utc),
    )
    return [check], [item]


def _parse_configuration(path: Path):
    text = path.read_text(encoding="utf-8", errors="strict")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    if suffix == ".toml":
        import tomllib
        return tomllib.loads(text)
    if suffix in {".ini", ".cfg"}:
        import configparser
        parser = configparser.ConfigParser()
        parser.read_string(text)
        return {section: dict(parser[section]) for section in parser.sections()}
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError:
            return _parse_simple_yaml(text)
        return yaml.safe_load(text)
    raise ValueError("unsupported configuration type")


def _select(value, selector):
    current = value
    for part in str(selector or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"configuration selector not found: {selector}")
        current = current[part]
    return current


def _parse_simple_yaml(text: str) -> dict:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())] or raw.lstrip().startswith("-"):
            raise ValueError("The built-in YAML checker supports mappings only.")
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if ":" not in line:
            raise ValueError("Invalid YAML mapping line.")
        key, raw_value = (part.strip() for part in line.split(":", 1))
        if not key:
            raise ValueError("Invalid empty YAML key.")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if key in parent:
            raise ValueError(f"Duplicate YAML key: {key}")
        if not raw_value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _yaml_scalar(raw_value)
    return root


def _yaml_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value.split(" #", 1)[0].rstrip()


def _entry_evidence(entry, manifest, evidence_type, observation):
    return VerificationEvidence(
        evidence_type=evidence_type, source_identifier=manifest.manifest_hash,
        relevant_file_or_artifact=entry.normalized_relative_path,
        content_hash=entry.content_hash, observation=observation,
        provenance=(entry.normalized_relative_path, manifest.scanner_policy_version),
        observed_at=datetime.now(timezone.utc),
    )


def _aggregate(checks):
    if any(item.outcome == "failed" for item in checks):
        return VerifierOutcome.FAILED
    if any(item.outcome == "inconclusive" for item in checks):
        return VerifierOutcome.INCONCLUSIVE
    return VerifierOutcome.PASSED


def _summary(outcome, checks):
    return f"Deterministic verification {outcome.value}: {sum(item.outcome == 'passed' for item in checks)} passed, {sum(item.outcome == 'failed' for item in checks)} failed, {sum(item.outcome == 'inconclusive' for item in checks)} inconclusive."


def _inconclusive(checker, reason):
    return PerformedCheck(checker=checker, checker_version=VERIFIER_VERSION, inputs=(), condition=reason, observed_value="not checked", expected_value_or_rule="declared deterministic checker inputs", outcome="inconclusive")


def _failed(checker, path, observed, expected):
    return PerformedCheck(checker=checker, checker_version=VERIFIER_VERSION, inputs=(path,), condition=expected, observed_value=observed, expected_value_or_rule=expected, outcome="failed")


def _bounded_json(value):
    return json.dumps(value, sort_keys=True, default=str)[:2000]


def _symbol_name(value):
    return re.split(r"[:.#]", str(value))[-1]


def _canonical_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "ProjectVerifierError", "assert_verifier_result_fresh", "run_deterministic_verifier",
]
