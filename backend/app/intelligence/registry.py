from __future__ import annotations

from typing import Any


def intelligence_components() -> list[dict[str, Any]]:
    return [
        _component(
            "slm_gateway",
            "Draft natural-language explanations, code suggestions, and command suggestions.",
            ["user prompts", "RAG snippets", "conversation memory summaries", "runtime policy context"],
            ["assistant text", "suggested code", "suggested commands"],
            "advisory",
            "Use fallback if unavailable, unsafe, or low confidence.",
            "intelligence.slm_gateway.used",
            confidence="Must not be used as authority for writes, path safety, credentials, or execution.",
        ),
        _component(
            "specialist_router",
            "Classify task intent and choose an advisory specialist.",
            ["user message", "optional routing context"],
            ["intent label", "specialist name", "confidence"],
            "advisory",
            "Fall back to deterministic rule routing on low confidence or missing model.",
            "intelligence.specialist_router.routed",
            confidence="High confidence can influence routing only; it cannot approve actions.",
        ),
        _component(
            "promoted_specialist_models",
            "Provide advisory ML predictions for trained specialist tasks.",
            ["validated text features", "approved model artifacts"],
            ["classification", "confidence", "metadata"],
            "advisory",
            "Fall back to deterministic classifier/rules when no promoted model or low confidence.",
            "intelligence.specialist_model.predicted",
            confidence="Promotion requires quality gates; low confidence cannot approve changes.",
        ),
        _component(
            "rag_search",
            "Ground project-specific answers in indexed local source context.",
            ["safe indexed files", "user query"],
            ["snippets", "paths", "line ranges", "scores"],
            "advisory",
            "If missing or weak, answer with uncertainty or deterministic guidance.",
            "intelligence.rag.searched",
            confidence="Positive-score sources are needed for grounded status.",
        ),
        _component(
            "dataset_profiler",
            "Deterministically inspect dataset schema, sample rows, and assignment suitability.",
            ["approved .csv/.txt/.tsv file path"],
            ["profile", "columns", "warnings", "suitability flags"],
            "authoritative",
            "Reject unsafe paths or unsupported files with clear errors.",
            "intelligence.dataset_profiled",
            confidence="Deterministic rules only; no model confidence involved.",
        ),
        _component(
            "assignment_copilot",
            "Convert assignment brief and dataset profile into guidance, evidence, report, and workspace plans.",
            ["assignment text/document", "optional dataset profile", "approved workspace path"],
            ["task checklist", "evidence checklist", "report skeleton", "safe suggestions"],
            "advisory",
            "Use deterministic extraction and placeholders; never invent results.",
            "intelligence.assignment_copilot.generated",
            confidence="Guidance only; actual submission readiness requires user-provided evidence.",
        ),
        _component(
            "workers_jobs",
            "Track slow or background jobs with status and failure reasons.",
            ["allowlisted job payloads", "safe paths"],
            ["job status", "logs", "result", "failure reason"],
            "controlled",
            "Failed jobs report reasons; cancelled jobs stop safely.",
            "intelligence.worker.job_event",
            confidence="Workers do not override safety policy or model governance.",
        ),
        _component(
            "deterministic_safety_gates",
            "Validate paths, writes, command suggestions, credentials, overwrites, and runtime plans.",
            ["paths", "commands", "write requests", "runtime plans"],
            ["allow/block/downgrade decision", "reason", "warnings"],
            "authoritative",
            "Block unsafe operations and require explicit user control for writes.",
            "intelligence.safety_gate.checked",
            confidence="Always authoritative; models cannot bypass safety gates.",
        ),
    ]


def _component(
    component_id: str,
    purpose: str,
    allowed_inputs: list[str],
    allowed_outputs: list[str],
    role: str,
    fallback_behavior: str,
    audit_event_type: str,
    *,
    confidence: str,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "purpose": purpose,
        "allowed_inputs": allowed_inputs,
        "allowed_outputs": allowed_outputs,
        "role": role,
        "confidence_requirements": confidence,
        "fallback_behavior": fallback_behavior,
        "audit_event_type": audit_event_type,
    }
