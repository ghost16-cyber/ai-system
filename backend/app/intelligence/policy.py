from __future__ import annotations

from typing import Any


def model_use_policy() -> dict[str, Any]:
    rules = [
        _rule("path_safety_deterministic", "Path safety is always deterministic.", "authoritative", ["path_utils", "workspace root validation"]),
        _rule("file_writing_controlled", "File writing is always controlled by deterministic validation and explicit user action.", "authoritative", ["safe writer", "overwrite flag", "path traversal checks"]),
        _rule("slm_suggest_only", "SLMs may suggest code or commands but cannot execute them or approve them.", "advisory", ["SLM gateway", "safe suggestion mode"]),
        _rule("specialist_ml_classify_only", "Specialist ML may classify task type but cannot approve actions.", "advisory", ["specialist router", "promoted specialist model"]),
        _rule("rag_before_project_answer", "RAG should be used before answering project-specific questions when an index exists.", "advisory", ["RAG search", "source grounding metadata"]),
        _rule("low_confidence_fallback", "Low-confidence model output must fall back to deterministic guidance or ask the user.", "authoritative", ["confidence thresholds", "fallback routing"]),
        _rule("credentials_deterministic", "Credential detection and redaction are deterministic; models cannot decide credential safety.", "authoritative", ["credential redaction", "safe file writers"]),
        _rule("command_execution_forbidden", "Commands are suggested only in Assignment Copilot and chat safety flows unless a separate explicit execution system authorizes them.", "authoritative", ["command analyzer", "runtime policy"]),
    ]
    return {
        "version": "phase-94-model-use-policy",
        "summary": "Models are advisory. Deterministic rules own safety, writes, paths, credentials, overwrite approval, and command execution.",
        "rules": rules,
        "low_confidence_thresholds": {
            "specialist_router": 0.6,
            "rag_grounding": "at least one positive-score source for grounded",
            "slm": "fallback or ask user when gateway is unavailable, unsafe, or uncertain",
        },
        "automatic_promotion_allowed": False,
        "slm_defaults_changed": False,
    }


def _rule(rule_id: str, statement: str, authority: str, enforced_by: list[str]) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "statement": statement,
        "authority": authority,
        "enforced_by": enforced_by,
        "audit_event_type": f"policy.{rule_id}",
    }
