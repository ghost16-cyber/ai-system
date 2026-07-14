from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from backend.app.folders.reader import ReadLimits, read_project_file
from backend.app.folders.safety import safe_relative_path
from backend.app.project_analysis.inventory import file_hashes
from backend.app.project_analysis.model_synthesis.contracts import (
    REQUEST_VERSION, SynthesisRequest, parse_synthesis_response, response_contract_description,
)
from backend.app.project_analysis.model_synthesis.evidence import build_evidence_package, evidence_hash, evidence_summary
from backend.app.project_analysis.model_synthesis.gateway import SynthesisGateway, SynthesisGatewayError
from backend.app.project_analysis.models import ProjectAnalysisError, confidence_level
from backend.app.project_analysis.synthesis import validate_contract
from backend.app.project_analysis.validation import prevalidate_virtual_files


MAX_CLARIFICATION_CYCLES = 2


class ModelSynthesisError(ProjectAnalysisError):
    def __init__(self, message: str, *, code: str, attempt: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.attempt = attempt


def synthesize_model_patch(
    root: str | Path,
    job: dict[str, Any],
    gateway: SynthesisGateway,
    *,
    attempt_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    attempt_id = uuid4().hex
    request_id = uuid4().hex
    started = _now()
    evidence = build_evidence_package(root, job)
    request = SynthesisRequest(
        contract_version=REQUEST_VERSION, request_id=request_id, job_id=str(job["job_id"]),
        conversation_id=str(job["conversation_id"]), folder_access_id=str(job["folder_access_id"]),
        root_fingerprint=str(job["root_fingerprint"]), analysis_id=evidence.analysis_id,
        index_version=evidence.index_version, evidence=evidence,
        output_contract=response_contract_description(),
    )
    payload = request.model_dump_json(exclude_none=False)
    attempt = _attempt(job, attempt_id, request_id, gateway, evidence, payload, started)
    if attempt_sink is not None:
        attempt_sink(dict(attempt))
    try:
        result = gateway.generate(payload)
    except SynthesisGatewayError as error:
        attempt.update(status=error.code, completed_at=_now(), uncertainty_reasons=[str(error)])
        raise ModelSynthesisError(str(error), code=error.code, attempt=attempt) from error
    attempt.update(provider=result.provider, model=result.model, usage=_safe_usage(result.usage), response_hash=_sha(result.raw_response))
    try:
        response = parse_synthesis_response(result.raw_response)
        attempt["response_contract_version"] = response.contract_version
        if response.request_id != request_id:
            raise ProjectAnalysisError("The coding model response is bound to a different synthesis request.")
        if response.requires_clarification:
            question = _safe_question(str(response.clarification_question or ""))
            if int(job.get("synthesis_clarification_count") or 0) >= MAX_CLARIFICATION_CYCLES:
                attempt.update(status="clarification_limit", completed_at=_now(), clarification={"question": question},
                               uncertainty_reasons=["The bounded model clarification limit was reached."])
                raise ModelSynthesisError(
                    "The model-assisted synthesis clarification limit was reached; the job remains plan-only.",
                    code="clarification_limit", attempt=attempt,
                )
            attempt.update(status="needs_clarification", completed_at=_now(), clarification={"question": question},
                           uncertainty_reasons=list(response.uncertainties))
            raise ModelSynthesisError(question, code="needs_clarification", attempt=attempt)
        contract = _normalize_response(root, job, evidence, response)
        validate_contract(contract)
        prevalidation = prevalidate_virtual_files(root, dict(job["analysis_index"]), contract)
        confidence = _confidence(job, evidence, response, prevalidation)
        attempt.update(validation=prevalidation, confidence=confidence,
                       uncertainty_reasons=[*response.uncertainties, *confidence["reasons"]])
        if confidence["level"] == "low":
            attempt.update(status="confidence_rejected", completed_at=_now())
            raise ModelSynthesisError(
                "Independent confidence remained low after model synthesis; no patch preview was created.",
                code="confidence_rejected", attempt=attempt,
            )
    except ModelSynthesisError:
        raise
    except ProjectAnalysisError as error:
        attempt.update(status="rejected", completed_at=_now(), uncertainty_reasons=[str(error)])
        raise ModelSynthesisError(str(error), code="malformed_or_unsafe", attempt=attempt) from error
    changes = [{
        "path": item["relative_path"], "operation": item["operation"],
        "content": item.get("content", ""), "explanation": item["reason"],
    } for item in contract["operations"]]
    warnings = list(dict.fromkeys([*response.uncertainties, *response.assumptions,
                                  *([] if confidence["level"] == "high" else ["Model-assisted synthesis passed with medium independent confidence; review the preview carefully."])]))
    attempt.update(status="validated", completed_at=_now(), assumptions=list(response.assumptions),
                   uncertainty_reasons=warnings)
    synthesis = {
        "attempt_id": attempt_id, "status": "validated", "strategy": "model_assisted",
        "contract_version": response.contract_version, "provider": result.provider, "model": result.model,
        "evidence": evidence_summary(evidence), "confidence": confidence,
        "assumptions": list(response.assumptions), "warnings": warnings,
        "requires_clarification": False, "summary": response.summary,
    }
    return {
        "changes": changes, "contract": contract, "prevalidation": prevalidation,
        "analysis_context": {"analysis_id": evidence.analysis_id, "index_version": evidence.index_version,
                             "source_hashes": contract["source_hashes"], "confidence": confidence["level"],
                             "prevalidation": prevalidation, "synthesis_attempt_id": attempt_id,
                             "synthesis_contract_version": response.contract_version},
        "synthesis": synthesis, "synthesis_attempt": attempt,
    }


def _normalize_response(root: str | Path, job: dict[str, Any], evidence: Any, response: Any) -> dict[str, Any]:
    allowed_modify = set(evidence.allowed_modify_paths)
    allowed_create = set(evidence.allowed_create_paths)
    allowed_delete = set(evidence.allowed_delete_paths)
    hashes = file_hashes(dict(job["analysis_index"]))
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for proposed in response.operations:
        path = safe_relative_path(proposed.path)
        if path in seen:
            raise ProjectAnalysisError("The coding model proposed conflicting duplicate file operations.")
        seen.add(path)
        kind = proposed.operation
        if kind == "modify" and path not in allowed_modify:
            raise ProjectAnalysisError(f"The coding model proposed an out-of-scope modification: {path}")
        if kind == "create" and path not in allowed_create:
            raise ProjectAnalysisError(f"The coding model proposed an unapproved created path: {path}")
        if kind == "delete" and path not in allowed_delete:
            raise ProjectAnalysisError(f"The coding model proposed an unapproved deletion: {path}")
        expected = hashes.get(path, "missing")
        if proposed.expected_sha256 != expected:
            raise ProjectAnalysisError(f"The coding model proposal is stale or incorrectly bound for {path}.")
        if kind == "modify":
            before = _read(root, path)
            content = proposed.content if proposed.strategy == "complete_content" else _apply_replacements(before, proposed.replacements)
        elif kind == "create":
            content = proposed.content
        else:
            content = ""
        operations.append({
            "operation": kind, "relative_path": path,
            "language": _language(path), "reason": proposed.rationale,
            "affected_symbols": list(proposed.affected_symbols), "content": content,
            "expected_relationships": [], "expected_tests": [],
            "confidence": "medium", "warnings": list(response.uncertainties),
        })
    operations.sort(key=lambda item: (item["relative_path"], item["operation"]))
    return {
        "job_id": job["job_id"], "conversation_id": job["conversation_id"],
        "folder_access_id": job["folder_access_id"], "root_fingerprint": job["root_fingerprint"],
        "analysis_id": evidence.analysis_id, "index_version": evidence.index_version,
        "source_hashes": {item["relative_path"]: hashes.get(item["relative_path"], "missing") for item in operations},
        "requested_operation": "controlled_model_assisted_multi_file_update",
        "operations": operations,
        "expected_relationships": [str(item.get("relationship_type") or "static relationship") for item in evidence.relationships[:20]],
        "expected_tests": list(evidence.impacted_tests),
        "confidence": "medium", "warnings": list(response.uncertainties),
    }


def _apply_replacements(before: str, replacements: list[Any]) -> str:
    lines = before.splitlines(keepends=True)
    ordered = sorted(replacements, key=lambda item: (item.start_line, item.end_line), reverse=True)
    previous_start = len(lines) + 1
    for replacement in ordered:
        if replacement.end_line >= previous_start or replacement.end_line > len(lines):
            raise ProjectAnalysisError("The coding model proposed overlapping or out-of-range replacements.")
        actual = "".join(lines[replacement.start_line - 1:replacement.end_line])
        if actual != replacement.expected_text:
            raise ProjectAnalysisError("The coding model exact replacement anchor did not match current evidence.")
        lines[replacement.start_line - 1:replacement.end_line] = [replacement.replacement_text]
        previous_start = replacement.start_line
    return "".join(lines)


def _confidence(job: dict[str, Any], evidence: Any, response: Any, validation: dict[str, Any]) -> dict[str, Any]:
    base = {"high": 0.80, "medium": 0.64, "low": 0.34}.get(str((job.get("analysis") or {}).get("confidence", {}).get("level")), 0.34)
    reasons = ["Stage 6 structural confidence is authoritative."]
    score = base
    files = {item.path for item in evidence.excerpts}
    if files == set(evidence.allowed_modify_paths):
        score += 0.06
        reasons.append("Every modifiable path has bounded evidence.")
    if validation.get("status") == "passed":
        score += 0.08
        reasons.append("Stage 6 non-mutating prevalidation passed.")
    if response.assumptions:
        score -= min(0.18, 0.04 * len(response.assumptions))
        reasons.append("Model assumptions reduced confidence.")
    if response.uncertainties:
        score -= min(0.20, 0.05 * len(response.uncertainties))
        reasons.append("Unresolved model uncertainty reduced confidence.")
    if any(item.operation == "delete" for item in response.operations):
        score -= 0.12
        reasons.append("Deletion is higher risk.")
    if len(response.operations) > 6:
        score -= 0.10
        reasons.append("A larger coherent change set reduced confidence.")
    if response.model_confidence == "high":
        score += 0.01
    score = max(0.0, min(score, 0.95))
    return {"level": confidence_level(score), "score": round(score, 3), "reasons": reasons,
            "model_claim": response.model_confidence}


def _attempt(job: dict[str, Any], attempt_id: str, request_id: str, gateway: Any, evidence: Any, payload: str, started: str) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id, "request_id": request_id, "job_id": job["job_id"],
        "conversation_id": job["conversation_id"], "folder_access_id": job["folder_access_id"],
        "root_fingerprint": job["root_fingerprint"], "analysis_id": evidence.analysis_id,
        "index_version": evidence.index_version, "request_contract_version": REQUEST_VERSION,
        "response_contract_version": None, "provider": gateway.provider, "model": gateway.model,
        "request_hash": _sha(payload), "response_hash": None, "evidence_hash": evidence_hash(evidence),
        "evidence_summary": evidence_summary(evidence), "usage": {}, "status": "running",
        "clarification": None, "validation": None, "confidence": None, "assumptions": [],
        "uncertainty_reasons": [], "patch_id": None, "started_at": started, "completed_at": None,
    }


def _safe_question(value: str) -> str:
    question = " ".join(value.split())[:500]
    lowered = question.lower()
    if not question or any(term in lowered for term in ("password", "secret", "api key", "token", "approve patch", "approve command")):
        raise ProjectAnalysisError("The coding model returned an unsafe clarification question.")
    return question


def _read(root: str | Path, path: str) -> str:
    record = read_project_file(root, path, limits=ReadLimits(max_bytes_per_file=250_000))
    if record.get("status") != "readable":
        raise ProjectAnalysisError(f"Model synthesis source is no longer readable: {path}")
    return str(record.get("text") or "")


def _language(path: str) -> str:
    return {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript", ".json": "json", ".yaml": "yaml", ".yml": "yaml"}.get(Path(path).suffix.lower(), "text")


def _safe_usage(value: dict[str, int]) -> dict[str, int]:
    return {str(key)[:40]: max(0, min(int(count), 10_000_000)) for key, count in list(value.items())[:8]}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["MAX_CLARIFICATION_CYCLES", "ModelSynthesisError", "synthesize_model_patch"]
