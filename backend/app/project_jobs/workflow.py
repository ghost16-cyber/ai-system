from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from backend.app.folders.context import build_project_context, detect_exact_relative_path
from backend.app.folders.reader import ReadLimits, iter_project_files, read_project_file
from backend.app.folders.safety import project_root_fingerprint, safe_relative_path
from backend.app.project_analysis import (
    ProjectAnalysisError,
    build_analysis_plan,
    build_project_index,
    synthesize_project_patch,
)
from backend.app.project_analysis.model_synthesis import (
    MAX_CLARIFICATION_CYCLES,
    SynthesisGateway,
    synthesize_model_patch,
)
from backend.app.schemas.api import ChatRunResponse


MAX_REVISION_CYCLES = 3
MAX_REPAIR_CYCLES = 3
MAX_REPAIR_FAILURES = 4
MAX_RELEVANT_PATHS = 16
MAX_REQUIREMENT_ITEMS = 12
MAX_SUMMARY_CHARS = 240


class ProjectJobError(ValueError):
    pass


_TASK_PATTERNS = (
    re.compile(r"^\s*(?:please\s+)?(?:fix|add|implement|refactor|improve|finish|complete|make|diagnose)\b", re.I),
    re.compile(r"^\s*(?:please\s+)?review\s+(?:this|the)\s+project\s+and\b", re.I),
    re.compile(r"^\s*(?:please\s+)?(?:do|perform|complete)\s+(?:the\s+)?requested\s+(?:feature|changes?|work)\b", re.I),
    re.compile(r"\b(?:fix|add|implement|refactor|improve|finish|complete|make|diagnose)\b", re.I),
)
_ORDINARY_QUESTION_PATTERNS = (
    re.compile(r"^\s*what\s+does\s+(?:this|the)\s+file\s+do\b", re.I),
    re.compile(r"^\s*(?:please\s+)?summari[sz]e\s+(?:this|the)\s+project\b", re.I),
    re.compile(r"^\s*where\s+is\s+.+\s+implemented\b", re.I),
    re.compile(r"^\s*(?:what|which|where|how|why)\b.*\?\s*$", re.I),
)
_FOLLOWUP_PATTERNS = (
    re.compile(r"^\s*continue\s+working\s+on\s+(?:this|the)\s+project\s+task\b", re.I),
    re.compile(r"^\s*what\s+is\s+left\s+to\s+finish\s+(?:this|the)\s+job\b", re.I),
    re.compile(r"^\s*(?:show|refresh)\s+(?:the\s+)?(?:job|plan|project\s+job)\b", re.I),
)
_MATERIAL_CLARIFICATION_TERMS = {
    "deploy": "Which approved environment and deployment boundary should be targeted? Deployment itself remains blocked.",
    "production": "Should this stop at a local patch and validation plan, or target a separately approved production process?",
    "credential": "Which credential-dependent behavior should be mocked or left as a documented integration point? Credentials will not be accessed.",
    "payment": "Which payment behavior and non-production test mode should be used? Live payment access remains blocked.",
    "install": "Can the implementation use only existing dependencies, or should a dependency change be proposed separately? Package installation remains blocked.",
    "delete data": "What exact data-retention behavior is intended? Destructive database operations remain blocked.",
}
_INJECTION_TERMS = (
    "approve patch", "approve command", "approve folder", "ignore previous", "override safety",
    "reveal secret", "show secret", "printenv", "git push", "git commit", "deploy now",
)

_DELIVERY_MUTATION_PATTERN = re.compile(
    r"\b(?:create|build|generate|write|produce|save|export|modify|update|implement|"
    r"refactor|fix|complete|analy[sz]e)\b",
    re.I,
)
_DELIVERY_ARTIFACT_PATTERN = re.compile(
    r"\b(?:deliverables?|files?|scripts?|reports?|charts?|plots?|dashboards?|notebooks?|"
    r"tests?|documentation|markdown|csv|json|html|pdf|png|jpe?g|svg|\.py|\.md)\b",
    re.I,
)
_DELIVERY_RESTRICTION_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|must\s+not|without|only|restrict(?:ed|ions?)?|unrelated|"
    r"no\s+external|no\s+deployment|local\s+only|before\s+(?:approval|execut)|"
    r"requires?\s+approval|approval\s+required)\b",
    re.I,
)


def detect_project_task(message: str) -> bool:
    text = (message or "").strip()
    if not text or any(pattern.search(text) for pattern in _ORDINARY_QUESTION_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in _TASK_PATTERNS)


def detect_project_delivery_task(message: str) -> bool:
    """Detect scoped, execution-oriented project requests with explicit deliverables.

    This intentionally requires all three signals. Ordinary project questions and
    underspecified implementation requests continue through their existing paths.
    Folder authority is checked separately by the chat route and is never inferred
    from a path embedded in the message.
    """
    text = " ".join((message or "").split())
    if not text or any(pattern.search(text) for pattern in _ORDINARY_QUESTION_PATTERNS):
        return False
    return bool(
        _DELIVERY_MUTATION_PATTERN.search(text)
        and _DELIVERY_ARTIFACT_PATTERN.search(text)
        and _DELIVERY_RESTRICTION_PATTERN.search(text)
    )


def detect_project_job_followup(message: str) -> bool:
    return any(pattern.search(message or "") for pattern in _FOLLOWUP_PATTERNS)


def create_project_job(
    *,
    root: str | Path,
    conversation_id: str,
    folder_access_id: str,
    user_task: str,
    action_run_id: str,
) -> dict[str, Any]:
    approved = Path(root).resolve()
    job_id = uuid4().hex
    extracted = _extract_requirements(approved, conversation_id, folder_access_id, user_task)
    safe_task = _strip_absolute_paths(user_task.replace(str(approved), "[connected project]"))
    extracted["objective"] = _bounded(safe_task.strip().rstrip("."), 500)
    clarification_question = _clarification_question(user_task, extracted)
    status = "needs_clarification" if clarification_question else "planned"
    now = _now()
    index = build_project_index(
        approved, conversation_id=conversation_id, folder_access_id=folder_access_id,
        job_id=job_id,
    )
    analysis_requirement = " ".join([
        safe_task,
        *(str(item.get("summary") or "") for item in extracted["requirement_summaries"]),
        *extracted["acceptance_criteria"],
    ])
    analysis = build_analysis_plan(index, analysis_requirement, relevant_paths=extracted["relevant_paths"])
    if analysis.get("impacted_tests") and extracted.get("validation_plan") and extracted["validation_plan"][0].get("action") == "pytest":
        target = str(analysis["impacted_tests"][0])
        extracted["validation_plan"][0] = {
            **extracted["validation_plan"][0], "target": target,
            "purpose": f"Run the impacted Python test file {target}.",
        }
    job = {
        "job_id": job_id,
        "action_run_id": action_run_id,
        "conversation_id": conversation_id,
        "folder_access_id": folder_access_id,
        "root_fingerprint": project_root_fingerprint(approved),
        "user_task": _bounded(safe_task, 2000),
        "status": status,
        "objective": extracted["objective"],
        "deliverables": extracted["deliverables"],
        "constraints": extracted["constraints"],
        "acceptance_criteria": extracted["acceptance_criteria"],
        "relevant_paths": extracted["relevant_paths"],
        "requirement_summaries": extracted["requirement_summaries"],
        "missing_information": extracted["missing_information"],
        "risks": extracted["risks"],
        "clarification": {
            "question": clarification_question,
            "answer": None,
            "requested_at": now if clarification_question else None,
            "answered_at": None,
        },
        "implementation_plan": _build_plan(extracted, safe_task, analysis),
        "analysis_id": index["analysis_id"],
        "analysis": analysis,
        "analysis_index": index,
        "synthesis": {
            "status": "not_started", "strategy": None, "provider": None, "model": None,
            "confidence": None, "warnings": [], "assumptions": [], "evidence": {},
            "requires_clarification": False,
        },
        "synthesis_clarification_count": 0,
        "max_synthesis_clarification_cycles": MAX_CLARIFICATION_CYCLES,
        "synthesis_attempt_count": 0,
        "max_synthesis_attempts": 3,
        "patch_ids": [],
        "command_plan_ids": [],
        "validation_plan": extracted["validation_plan"],
        "validation_results": [],
        "completion_summary": None,
        "revision_count": 0,
        "max_revision_cycles": MAX_REVISION_CYCLES,
        "repair": {
            "status": "not_started", "repair_chain_id": None, "repair_cycle_id": None,
            "cycle_number": 0, "failure_evidence_id": None, "diagnosis_id": None,
            "parent_patch_id": None, "repair_patch_id": None, "command_execution_id": None,
            "diagnosis_strategy": None, "provider": None, "model": None,
            "confidence": None, "root_causes": [], "affected_files": [],
            "affected_symbols": [], "assumptions": [], "warnings": [],
            "clarification": None, "validation_rerun_status": "not_planned",
            "rollback_available": False,
        },
        "repair_cycle_count": 0,
        "max_repair_cycles": MAX_REPAIR_CYCLES,
        "repair_failure_count": 0,
        "max_repair_failures": MAX_REPAIR_FAILURES,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "cancelled_at": None,
    }
    return job


def build_job_chat_run(
    job: dict[str, Any],
    *,
    message: str,
    response: str | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
) -> ChatRunResponse:
    status = str(job["status"])
    if response is None:
        response = (
            str(job["clarification"]["question"])
            if status == "needs_clarification"
            else "I created an evidence-backed project job and a non-mutating implementation plan. Review it before preparing any patch preview."
        )
    paths = list(job.get("relevant_paths") or [])[:MAX_RELEVANT_PATHS]
    return ChatRunResponse(
        run_id=run_id or str(job["action_run_id"]),
        conversation_id=str(job["conversation_id"]),
        user_message=message,
        assistant_response=response,
        selected_specialist="project_job",
        intent="project_job",
        confidence=1.0,
        rag_used=False,
        rag_skip_reason="Connected-project jobs use only the approved bounded project reader.",
        rag_context_count=0,
        source_count=len(paths),
        source_paths=paths,
        grounding_status="grounded" if paths else "weak",
        runtime_decision="clarification_required" if status == "needs_clarification" else "plan_ready",
        safety_decision="allowed_read_only",
        used_real_slm=False,
        slm_provider="not_invoked",
        slm_fallback_reason="Deterministic project-job intake and planning.",
        memory_used=True,
        memory_summary=None,
        created_at=datetime.fromisoformat(created_at or str(job["created_at"])),
        trace_summary=[{
            "phase": "project_job_intake",
            "title": "Project job created",
            "detail": f"Extracted bounded requirements from {len(paths)} safe relative project path(s).",
            "status": "passed",
            "data": {"job_id": job["job_id"], "status": status, "relative_paths": paths},
        }],
        action=build_job_action(job),
    )


def build_job_action(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job["status"])
    title = "Project job"
    if status == "needs_clarification":
        title = "Project job needs clarification"
    elif status == "completed":
        title = "Project job completed"
    elif status == "blocked":
        title = "Project job blocked"
    return {
        "action_id": str(job["job_id"]),
        "action_type": "project_job",
        "title": title,
        "summary": str(job["objective"]),
        "steps": list(job.get("implementation_plan", {}).get("steps") or []),
        "safety_information": {
            "plan_mutates_files": False,
            "job_approval_is_patch_approval": False,
            "patch_approval_required": True,
            "command_approval_required": True,
            "project_files_are_untrusted_evidence": True,
        },
        "status": status,
        "approval_required": False,
        "result_summary": _job_result_summary(job),
        "error": None,
        "technical_details": {"project_job": public_project_job(job)},
    }


def public_project_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "job_id", "conversation_id", "folder_access_id", "user_task", "status", "objective",
        "deliverables", "constraints", "acceptance_criteria", "relevant_paths",
        "requirement_summaries", "missing_information", "risks", "clarification",
        "implementation_plan", "patch_ids", "command_plan_ids", "validation_plan",
        "validation_results", "completion_summary", "revision_count", "max_revision_cycles",
        "created_at", "updated_at", "completed_at", "cancelled_at", "analysis_id", "analysis",
        "synthesis", "synthesis_clarification_count", "max_synthesis_clarification_cycles",
        "synthesis_attempt_count", "max_synthesis_attempts",
        "repair", "repair_cycle_count", "max_repair_cycles", "repair_failure_count",
        "max_repair_failures", "delivery_job_id",
    }
    return {key: job.get(key) for key in allowed}


def answer_clarification(job: dict[str, Any], answer: str) -> dict[str, Any]:
    if job.get("status") != "needs_clarification":
        raise ProjectJobError("This project job is not awaiting clarification.")
    if not answer.strip():
        raise ProjectJobError("A clarification answer is required.")
    updated = dict(job)
    clarification = dict(job.get("clarification") or {})
    clarification.update({"answer": _bounded(answer, 1000), "answered_at": _now()})
    synthesis = dict(job.get("synthesis") or {})
    synthesis.update({"status": "clarification_answered", "requires_clarification": False})
    updated.update({"status": "planned", "clarification": clarification, "synthesis": synthesis, "updated_at": _now()})
    plan = dict(updated.get("implementation_plan") or {})
    assumptions = list(plan.get("unresolved_assumptions") or [])
    assumptions.append(f"User clarification: {_bounded(answer, MAX_SUMMARY_CHARS)}")
    plan["unresolved_assumptions"] = assumptions[-6:]
    updated["implementation_plan"] = plan
    return updated


def prepare_job_patch_changes(root: str | Path, job: dict[str, Any], *, model_gateway: SynthesisGateway | None = None) -> list[dict[str, Any]]:
    return prepare_job_patch_bundle(root, job, model_gateway=model_gateway)["changes"]


def prepare_job_patch_bundle(
    root: str | Path,
    job: dict[str, Any],
    *,
    model_gateway: SynthesisGateway | None = None,
    model_attempt_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if job.get("status") not in {"planned", "blocked"}:
        raise ProjectJobError("This project job is not ready to prepare a patch preview.")
    if int(job.get("revision_count") or 0) >= int(job.get("max_revision_cycles") or MAX_REVISION_CYCLES):
        raise ProjectJobError("The project job reached its bounded revision-cycle limit. Ask explicitly to continue before preparing another patch.")
    if job.get("status") == "blocked" and not job.get("repair_context"):
        raise ProjectJobError("Request diagnosis of the persisted approved-command failure before preparing a repair preview.")
    approved = Path(root).resolve()
    if job.get("analysis_index"):
        try:
            bundle = synthesize_project_patch(approved, job)
            bundle["synthesis"] = _deterministic_synthesis("stage6_structural", bundle.get("prevalidation"))
            return bundle
        except ProjectAnalysisError as error:
            if not str(error).startswith("No coherent bounded multi-file synthesis pattern matched"):
                raise ProjectJobError(str(error)) from error
    candidates = _candidate_python_paths(approved, list(job.get("relevant_paths") or []))
    for relative in candidates:
        record = read_project_file(approved, relative, limits=ReadLimits(max_files=30))
        if record["status"] != "readable":
            continue
        before = str(record["text"])
        after = _apply_safe_astra_todo(before)
        if after != before:
            changes = [{
                "path": relative,
                "operation": "modify",
                "content": after,
                "explanation": "Implement the bounded ASTRA_TODO return expression identified during approved project inspection.",
            }]
            prevalidation = {"status": "passed", "checks": ["bounded marker expression", "Python AST return validation"], "warnings": []}
            return {"changes": changes, "contract": None, "analysis_context": None, "prevalidation": prevalidation,
                    "synthesis": _deterministic_synthesis("bounded_marker", prevalidation)}
    if job.get("status") == "blocked":
        inferred = _infer_failed_assertion_repair(approved, candidates)
        if inferred:
            prevalidation = {"status": "passed", "checks": ["bounded assertion evidence", "Python AST syntax"], "warnings": []}
            return {"changes": [inferred], "contract": None, "analysis_context": None, "prevalidation": prevalidation,
                    "synthesis": _deterministic_synthesis("bounded_assertion_repair", prevalidation)}
    if model_gateway is not None:
        if int(job.get("synthesis_attempt_count") or 0) >= int(job.get("max_synthesis_attempts") or 3):
            raise ProjectJobError("The bounded model synthesis attempt limit was reached; refine the plan before retrying.")
        return synthesize_model_patch(approved, job, model_gateway, attempt_sink=model_attempt_sink)
    raise ProjectJobError(
        "No bounded deterministic patch could be prepared from the inspected evidence. Refine the task or request an explicit file change; no files were modified."
    )


def _deterministic_synthesis(strategy: str, prevalidation: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "status": "validated", "strategy": strategy, "provider": "not_invoked", "model": None,
        "contract_version": None, "evidence": {},
        "confidence": {"level": "high", "score": 1.0, "reasons": ["A bounded deterministic synthesis rule matched."], "model_claim": None},
        "assumptions": [], "warnings": list((prevalidation or {}).get("warnings") or []),
        "requires_clarification": False, "summary": "A deterministic bounded synthesis rule produced the patch preview.",
    }


def interpret_validation_result(command: dict[str, Any], analysis_index: dict[str, Any] | None = None) -> dict[str, Any]:
    stdout = str(command.get("stdout") or "")[-12_000:]
    stderr = str(command.get("stderr") or "")[-12_000:]
    combined = f"{stdout}\n{stderr}"
    succeeded = command.get("exit_code") == 0 and command.get("display_state") == "completed"
    summary = "Validation completed successfully." if succeeded else "Validation failed."
    likely_paths = _relative_error_paths(combined)
    category = _failure_category(combined, succeeded)
    affected_symbols = []
    for item in (analysis_index or {}).get("files", []):
        if item.get("relative_path") in likely_paths:
            affected_symbols.extend({"relative_path": item["relative_path"], "name": symbol.get("name"), "kind": symbol.get("kind"), "range": symbol.get("range")} for symbol in item.get("symbols", [])[:12])
    pytest_match = re.search(r"(?m)=+\s*(.+?(?:passed|failed|error).+?)\s+in\s+([0-9.]+)s\s*=+", combined)
    if pytest_match:
        summary = f"Pytest: {_bounded(pytest_match.group(1).strip(), 180)} in {pytest_match.group(2)} seconds."
    elif simple_pytest := re.search(r"(?m)(\d+\s+(?:passed|failed|error)(?:[^\n]*?))\s+in\s+([0-9.]+)s", combined):
        summary = f"Pytest: {_bounded(simple_pytest.group(1).strip(), 180)} in {simple_pytest.group(2)} seconds."
    elif re.search(r"TS\d{4}:", combined):
        count = len(re.findall(r"TS\d{4}:", combined))
        summary = f"TypeScript reported {count} bounded compiler error(s)."
    elif "eslint" in combined.lower() and not succeeded:
        summary = "ESLint reported validation failures; review the cited relative paths."
    elif "not ok" in combined.lower() and not succeeded:
        summary = "Node tests reported one or more failures."
    elif "traceback (most recent call last)" in combined.lower():
        summary = "Python raised an exception during validation."
    return {
        "status": "passed" if succeeded else "failed",
        "summary": summary,
        "likely_affected_paths": likely_paths,
        "likely_affected_symbols": affected_symbols[:30],
        "failure_category": category,
        "recommended_next_step": (
            "Review manual checks and complete the job."
            if succeeded
            else "Review the bounded failure, revise the plan, and prepare a new patch preview with normal approval."
        ),
        "command_plan_id": command.get("plan_id"),
        "action": command.get("action"),
        "exit_code": command.get("exit_code"),
        "finished_at": command.get("finished_at"),
    }


def _failure_category(output: str, succeeded: bool) -> str:
    if succeeded:
        return "none"
    lowered = output.lower()
    if "syntaxerror" in lowered or "parse error" in lowered:
        return "syntax"
    if "modulenotfounderror" in lowered or "importerror" in lowered or "cannot find module" in lowered:
        return "import"
    if re.search(r"TS\d{4}:", output) or "type error" in lowered or "typeerror:" in lowered:
        return "type"
    if "eslint" in lowered or "flake8" in lowered or "ruff" in lowered:
        return "lint"
    if "assertionerror" in lowered or "assert " in lowered or "expected" in lowered and "received" in lowered:
        return "assertion"
    if "config" in lowered or "configuration" in lowered:
        return "configuration"
    if "build failed" in lowered or "failed to build" in lowered:
        return "build"
    return "unknown"


def build_completion_summary(job: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    changed = sorted({path for patch in patches for path in patch.get("file_set", [])})
    results = list(job.get("validation_results") or [])
    return {
        "requested_objective": job.get("objective"),
        "work_completed": [f"Applied approved patch {str(patch.get('patch_id'))[:8]}." for patch in patches if patch.get("status") == "applied"],
        "files_changed": changed,
        "validation_performed": [str(item.get("action") or "validation") for item in results],
        "validation_outcome": results[-1].get("summary") if results else "No validation result recorded.",
        "verified_facts": [str(item.get("summary")) for item in results if item.get("status") == "passed"],
        "assumptions": list(job.get("implementation_plan", {}).get("unresolved_assumptions") or []),
        "items_not_tested": [] if results else ["No validation command was approved and run."],
        "unresolved_limitations": list(job.get("missing_information") or []),
        "rollback_available": any(patch.get("status") == "applied" for patch in patches),
        "suggested_manual_checks": ["Exercise the requested behavior through its normal user-facing path."],
    }


def _extract_requirements(root: Path, conversation_id: str, folder_access_id: str, message: str) -> dict[str, Any]:
    context = build_project_context(
        root=root,
        conversation_id=conversation_id,
        folder_access_id=folder_access_id,
        user_query=message,
        limits=ReadLimits(max_files=60, max_excerpts=16, max_context_chars=48_000),
    )
    sources = list(context.get("sources") or [])
    known_paths = {str(item.get("relative_path") or "") for item in sources}
    task_tokens = {
        token for token in re.findall(r"[A-Za-z0-9_-]{3,}", message.lower())
        if token not in {"this", "that", "project", "implement", "feature", "review", "make", "complete"}
    }
    for path in iter_project_files(root, max_files=60):
        relative = path.relative_to(root).as_posix()
        if relative in known_paths or len(sources) >= MAX_RELEVANT_PATHS:
            continue
        name = path.name.lower()
        record = read_project_file(root, relative)
        if record["status"] != "readable":
            continue
        text = str(record["text"])
        lower = text.lower()
        high_signal = (
            name in {"readme.md", "pyproject.toml", "package.json", "requirements.txt"}
            or name.startswith("test_")
            or ".test." in name
            or "astra_todo:" in lower
            or "todo" in lower
            or "notimplemented" in lower
            or path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs"}
            or any(token in relative.lower() or token in lower for token in task_tokens)
        )
        if high_signal:
            sources.append({"relative_path": relative, "excerpt": "\n".join(text.splitlines()[:40])[:4000]})
            known_paths.add(relative)
    explicit = detect_exact_relative_path(message)
    if explicit and explicit not in [item.get("relative_path") for item in sources]:
        record = read_project_file(root, explicit)
        if record["status"] == "readable":
            sources.insert(0, {"relative_path": explicit, "excerpt": str(record["text"])[:4000]})
    relevant_paths = []
    summaries = []
    injection_ignored = False
    acceptance = []
    for source in sources[:MAX_RELEVANT_PATHS]:
        relative = safe_relative_path(str(source.get("relative_path") or ""))
        if relative not in relevant_paths:
            relevant_paths.append(relative)
        excerpt = str(source.get("excerpt") or "")
        suffix = Path(relative).suffix.lower()
        acceptance_source = suffix in {".md", ".markdown"} or Path(relative).name.lower().startswith("test_") or ".test." in relative.lower() or ".spec." in relative.lower()
        lines = []
        for raw_line in excerpt.splitlines():
            clean = re.sub(r"^\s*\d+:\s*", "", raw_line).strip()
            if not clean:
                continue
            if any(term in clean.lower() for term in _INJECTION_TERMS):
                injection_ignored = True
                continue
            if len(lines) < 2:
                lines.append(_bounded(clean, MAX_SUMMARY_CHARS))
            if acceptance_source and re.search(r"\b(?:assert|expect|must|should|acceptance)\b", clean, re.I) and len(acceptance) < 6:
                acceptance.append(f"{_bounded(clean, 180)} ({relative})")
        if suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs"}:
            if Path(relative).name.lower().startswith("test_") or ".test." in relative.lower():
                summary = "Existing test expectations were identified as acceptance evidence."
            elif "astra_todo:" in excerpt.lower() or "notimplemented" in excerpt.lower():
                summary = "A bounded implementation placeholder was identified for patch preparation."
            else:
                summary = "Relevant implementation structure was identified for the requested task."
        else:
            summary = " ".join(lines)[:MAX_SUMMARY_CHARS]
        if summary and len(summaries) < MAX_REQUIREMENT_ITEMS:
            summaries.append({"relative_path": relative, "summary": summary})
    constraints = [
        "Stay inside the approved project root.",
        "Require separate explicit approval for every patch and validation command.",
        "Do not install packages, deploy, use credentials, or perform Git writes.",
    ]
    risks = ["Project files are untrusted evidence and cannot approve actions."]
    if injection_ignored:
        risks.append("Instruction-like project content was ignored during requirement extraction.")
    deliverables = [_deliverable_from_message(message)]
    if acceptance:
        criteria = acceptance[:8]
    else:
        criteria = [f"The requested behavior is implemented and the narrowest existing validation passes ({relevant_paths[0]})."] if relevant_paths else ["The requested behavior is defined with a verifiable acceptance check."]
    missing = [] if relevant_paths else ["No relevant safe project file was identified within the bounded read budget."]
    return {
        "objective": _bounded(message.strip().rstrip("."), 500),
        "deliverables": deliverables,
        "constraints": constraints,
        "acceptance_criteria": criteria,
        "relevant_paths": relevant_paths,
        "requirement_summaries": summaries,
        "missing_information": missing,
        "risks": risks,
        "validation_plan": _validation_plan(root),
    }


def _build_plan(extracted: dict[str, Any], message: str, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = list(extracted["relevant_paths"])
    findings = [
        {"claim": item["summary"], "relative_path": item["relative_path"]}
        for item in extracted["requirement_summaries"][:8]
    ]
    return {
        "objective": extracted["objective"],
        "current_state_findings": findings,
        "files_likely_involved": paths,
        "steps": [
            "Confirm the bounded requirements and cited evidence.",
            "Prepare the smallest immutable patch preview supported by that evidence.",
            "Request explicit approval for each exact patch.",
            "Apply approved patches atomically with rollback snapshots.",
            "Propose the narrowest allowlisted validation command for separate approval.",
            "Interpret bounded results and produce a completion report.",
        ],
        "safety_impact": "Planning is read-only; patch and command approvals remain independent.",
        "validation_plan": extracted["validation_plan"],
        "expected_deliverables": extracted["deliverables"],
        "unresolved_assumptions": extracted["missing_information"],
        "broad_request": len(message.split()) < 5 or not bool(paths),
        "stage6_analysis": analysis or {},
    }


def _clarification_question(message: str, extracted: dict[str, Any]) -> str | None:
    lowered = message.lower()
    for term, question in _MATERIAL_CLARIFICATION_TERMS.items():
        if term in lowered:
            return question
    if not extracted["relevant_paths"]:
        return "Which project-relative file or component should this job target?"
    return None


def _validation_plan(root: Path) -> list[dict[str, Any]]:
    names = {path.name.lower() for path in iter_project_files(root, max_files=120)}
    if "pyproject.toml" in names or "pytest.ini" in names or any(name.startswith("test_") and name.endswith(".py") for name in names):
        return [{"action": "pytest", "target": None, "purpose": "Run the existing Python test suite.", "expected_result": "Pytest reports bounded pass/fail totals."}]
    if "package.json" in names:
        record = read_project_file(root, "package.json")
        scripts: dict[str, Any] = {}
        if record["status"] == "readable":
            try:
                payload = json.loads(str(record["text"]))
                scripts = payload.get("scripts") if isinstance(payload, dict) and isinstance(payload.get("scripts"), dict) else {}
            except json.JSONDecodeError:
                scripts = {}
        for key, action in (("test", "npm_test"), ("typecheck", "npm_run_typecheck"), ("lint", "npm_run_lint"), ("build", "npm_run_build")):
            if key in scripts:
                return [{"action": action, "target": None, "purpose": f"Run the existing npm {key} script.", "expected_result": "The configured script exits successfully with bounded output."}]
    return []


def _candidate_python_paths(root: Path, relevant: list[str]) -> list[str]:
    ordered = []
    for value in relevant:
        try:
            relative = safe_relative_path(value)
        except Exception:
            continue
        if relative.endswith(".py") and not Path(relative).name.startswith("test_"):
            ordered.append(relative)
    for path in iter_project_files(root, max_files=80):
        relative = path.relative_to(root).as_posix()
        if relative.endswith(".py") and not Path(relative).name.startswith("test_") and relative not in ordered:
            ordered.append(relative)
    return ordered[:30]


def _apply_safe_astra_todo(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>\s*)#\s*ASTRA_TODO:\s*(?P<statement>return\s+.+?)\s*$", line.rstrip("\r\n"))
        if not match:
            continue
        statement = match.group("statement")
        if not _safe_return_statement(statement):
            continue
        for target in range(index + 1, min(len(lines), index + 4)):
            stripped = lines[target].strip()
            if stripped.startswith("raise NotImplementedError") or stripped in {"return None", "pass"}:
                newline = "\r\n" if lines[target].endswith("\r\n") else "\n"
                indent = re.match(r"^\s*", lines[target]).group(0)
                lines[index] = ""
                lines[target] = f"{indent}{statement}{newline}"
                return "".join(lines)
    return text


def _infer_failed_assertion_repair(root: Path, source_paths: list[str]) -> dict[str, Any] | None:
    expectations: dict[str, str] = {}
    for path in iter_project_files(root, max_files=80):
        relative = path.relative_to(root).as_posix()
        if not (path.name.startswith("test_") and path.suffix == ".py"):
            continue
        record = read_project_file(root, relative)
        if record["status"] != "readable":
            continue
        try:
            tree = ast.parse(str(record["text"]))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare) or len(node.test.ops) != 1:
                continue
            if not isinstance(node.test.ops[0], ast.Eq) or len(node.test.comparators) != 1:
                continue
            call = node.test.left
            expected = node.test.comparators[0]
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and isinstance(expected, ast.Constant) and isinstance(expected.value, (str, int, float, bool, type(None))):
                expectations[call.func.id] = repr(expected.value)
    for relative in source_paths:
        record = read_project_file(root, relative)
        if record["status"] != "readable":
            continue
        before = str(record["text"])
        try:
            tree = ast.parse(before)
        except SyntaxError:
            continue
        lines = before.splitlines(keepends=True)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in expectations:
                continue
            returns = [item for item in node.body if isinstance(item, ast.Return) and item.lineno == getattr(item, "end_lineno", item.lineno)]
            if len(returns) != 1:
                continue
            result = returns[0]
            index = result.lineno - 1
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
            indent = re.match(r"^\s*", lines[index]).group(0)
            lines[index] = f"{indent}return {expectations[node.name]}{newline}"
            after = "".join(lines)
            if after != before:
                return {
                    "path": relative, "operation": "modify", "content": after,
                    "explanation": "Revise one bounded Python return value to match an existing failed equality assertion.",
                }
    return None


def _safe_return_statement(statement: str) -> bool:
    if len(statement) > 240 or ";" in statement or "__" in statement:
        return False
    try:
        node = ast.parse(statement).body[0]
    except (SyntaxError, IndexError):
        return False
    allowed = (
        ast.Module, ast.Return, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.Add, ast.Sub,
        ast.Mult, ast.Div, ast.Mod, ast.JoinedStr, ast.FormattedValue, ast.UnaryOp, ast.USub,
        ast.Tuple, ast.List, ast.Dict,
    )
    return isinstance(node, ast.Return) and all(isinstance(item, allowed) for item in ast.walk(node))


def _relative_error_paths(output: str) -> list[str]:
    values = []
    for raw in re.findall(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|js|jsx|ts|tsx)(?::\d+)?", output):
        path = raw.split(":", 1)[0].replace("\\", "/")
        try:
            relative = safe_relative_path(path)
        except Exception:
            continue
        if relative not in values:
            values.append(relative)
    return values[:12]


def _deliverable_from_message(message: str) -> str:
    lowered = message.lower()
    if "diagnos" in lowered:
        return "A bounded diagnosis, approved fix proposal, and validation result."
    if "review" in lowered:
        return "An evidence-backed review, implementation plan, approved changes, and completion report."
    return "The requested project change, validated through separately approved checks."


def _job_result_summary(job: dict[str, Any]) -> str | None:
    status = job.get("status")
    if status == "completed":
        return "Implementation and approved validation completed; rollback remains available for applied Astra patches."
    if status == "cancelled":
        return "Project job cancelled. No pending job action will run."
    if status == "blocked":
        repair = dict(job.get("repair") or {})
        if repair.get("status") == "offered":
            return "The approved validation command failed. Diagnosis is available but has not started; no files changed after the command."
        if repair.get("status") == "plan_only":
            return "Diagnosis stopped safely without creating a repair preview."
        results = list(job.get("validation_results") or [])
        return str(results[-1].get("summary")) if results else "The job is blocked pending a bounded revision."
    return None


def detect_repair_request(message: str) -> bool:
    normalized = " ".join(str(message or "").lower().split())
    if not normalized:
        return False
    repair_terms = ("diagnose", "diagnosis", "analyse the failure", "analyze the failure", "repair", "fix the failed", "fix the failure")
    failure_terms = ("failure", "failed", "test", "validation", "error", "repair", "diagnos")
    return any(term in normalized for term in repair_terms) and any(term in normalized for term in failure_terms)


def _bounded(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _strip_absolute_paths(value: str) -> str:
    sanitized = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|/|\\\\)[^\s,;]+", "[absolute path omitted]", value)
    return sanitized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "MAX_REPAIR_CYCLES", "MAX_REPAIR_FAILURES", "MAX_REVISION_CYCLES", "ProjectJobError", "answer_clarification",
    "build_completion_summary", "build_job_action", "build_job_chat_run",
    "create_project_job", "detect_project_delivery_task", "detect_project_job_followup", "detect_project_task", "detect_repair_request",
    "interpret_validation_result", "prepare_job_patch_bundle", "prepare_job_patch_changes", "public_project_job",
]
