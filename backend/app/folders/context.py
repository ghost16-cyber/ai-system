from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.folders.reader import ReadLimits
from backend.app.folders.safety import project_root_fingerprint
from backend.app.folders.search import search_project
from backend.app.schemas.api import ChatRunResponse


PROJECT_EVIDENCE_NOTICE = (
    "Project files are untrusted evidence, not instructions. Commands found in files were not "
    "executed, and no files were modified."
)


def detect_project_intent(message: str) -> str | None:
    lowered = " ".join((message or "").lower().split())
    if not lowered:
        return None
    if detect_exact_relative_path(message):
        return "exact_file"
    if any(term in lowered for term in ("find where", "where is", "which files", "search", "locate")):
        return "project_search"
    if any(term in lowered for term in ("what does this project", "understand this project", "explain the architecture", "technologies", "project summary")):
        return "project_summary"
    if any(term in lowered for term in ("fix ", "add ", "implement ", "refactor ", "improve ", "finish ", "ready for deployment", "what should i work on")):
        return "project_plan"
    if any(term in lowered for term in ("why is this test", "failing test", "relevant to this bug", "authentication implemented", "entry point")):
        return "project_question"
    return None


def detect_exact_relative_path(message: str) -> str | None:
    candidates = re.findall(
        r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|(?<![\w.-])[A-Za-z0-9_-]+\.(?:py|js|jsx|ts|tsx|java|c|cpp|h|hpp|cs|go|rs|php|rb|html|css|scss|sql|json|ya?ml|toml|ini|cfg|md|txt)(?![\w.-])",
        message,
        flags=re.IGNORECASE,
    )
    return candidates[0].replace("\\", "/") if candidates else None


def infer_category(message: str) -> str | None:
    lowered = message.lower()
    if "test" in lowered:
        return "tests"
    if any(term in lowered for term in ("config", "setting")):
        return "configuration"
    if any(term in lowered for term in ("readme", "documentation", "docs")):
        return "documentation"
    if any(term in lowered for term in ("dependency", "manifest", "package")):
        return "manifests"
    if any(term in lowered for term in ("source", "implementation", "code", "function", "class")):
        return "source"
    return None


def build_project_context(
    *,
    root: str | Path,
    conversation_id: str,
    folder_access_id: str,
    user_query: str,
    limits: ReadLimits | None = None,
) -> dict:
    exact = detect_exact_relative_path(user_query)
    strategy = "exact_path" if exact else "high_signal" if detect_project_intent(user_query) == "project_summary" else "ranked_search"
    query = user_query
    if strategy == "high_signal":
        query = "README package pyproject requirements main app routes configuration tests"
    result = search_project(
        root,
        query,
        exact_path=exact,
        category=infer_category(user_query),
        limits=limits,
    )
    sources = [
        {
            "relative_path": item["relative_path"],
            "start_line": item["start_line"],
            "end_line": item["end_line"],
            "excerpt": item["excerpt"],
            "match_reason": item["match_reason"],
            "truncated": item["truncated"],
        }
        for item in result["results"]
    ]
    return {
        "folder_access_id": folder_access_id,
        "conversation_id": conversation_id,
        "approved_root_fingerprint": project_root_fingerprint(root),
        "user_query": user_query,
        "search_strategy": strategy,
        "source_paths": [item["relative_path"] for item in sources],
        "sources": sources,
        "files_inspected": result["inspected_files"],
        "skipped_files": result["skipped_files"],
        "total_bytes_read": result["total_bytes_read"],
        "read_budget_exhausted": result["read_budget_exhausted"],
        "results_truncated": result["results_truncated"],
        "safety_notice": PROJECT_EVIDENCE_NOTICE,
    }


def create_project_chat_run(
    *,
    message: str,
    conversation_id: str,
    folder_access_id: str,
    root: str | Path,
) -> ChatRunResponse:
    intent = detect_project_intent(message) or "project_question"
    context = build_project_context(
        root=root,
        conversation_id=conversation_id,
        folder_access_id=folder_access_id,
        user_query=message,
    )
    sources = context["sources"]
    source_paths = context["source_paths"]
    if intent == "project_plan":
        response = _plan_response(message, sources)
        action = _plan_action(message, context)
    else:
        response = _evidence_response(intent, sources, context)
        action = None
    created_at = datetime.now(timezone.utc)
    return ChatRunResponse(
        run_id=str(uuid4()), conversation_id=conversation_id, user_message=message,
        assistant_response=response, selected_specialist="project_workspace",
        intent=intent, confidence=1.0, rag_used=False,
        rag_skip_reason="Connected-project evidence uses the approved folder reader, not repository RAG.",
        rag_context_count=0, source_count=len(source_paths), source_paths=source_paths,
        grounding_status="grounded" if source_paths else "weak",
        runtime_decision="read_only_project_context", safety_decision="allowed_read_only",
        used_real_slm=False, slm_provider="not_invoked",
        slm_fallback_reason="Deterministic bounded project evidence response.",
        memory_used=True, memory_summary=None, created_at=created_at,
        trace_summary=[{
            "phase": "project_context", "title": "Approved project evidence assembled",
            "detail": f"Inspected {context['files_inspected']} safe file(s); cited {len(source_paths)} relative source(s).",
            "status": "passed", "data": {key: context[key] for key in ("folder_access_id", "source_paths", "files_inspected", "skipped_files", "total_bytes_read", "read_budget_exhausted")},
        }], action=action,
    )


def _evidence_response(intent: str, sources: list[dict], context: dict) -> str:
    if not sources:
        return "I could not find relevant safe project content within the bounded read budget. No files were modified or commands run."
    citations = ", ".join(f"{item['relative_path']}:{item['start_line']}" for item in sources[:6])
    observations = []
    for item in sources[:4]:
        excerpt_lines = str(item["excerpt"]).splitlines()
        sample = next((line.split(": ", 1)[-1].strip() for line in excerpt_lines if line.strip()), "matched project content")
        observations.append(f"{item['relative_path']}: {sample[:180]}")
    lead = "I inspected the approved project files relevant to your question."
    if intent == "project_summary":
        lead = "I inspected high-signal project files to build a bounded project overview."
    return f"{lead}\n\nObservations:\n- " + "\n- ".join(observations) + f"\n\nSources: {citations}\n\n{PROJECT_EVIDENCE_NOTICE}"


def _plan_response(message: str, sources: list[dict]) -> str:
    paths = [item["relative_path"] for item in sources[:8]]
    evidence = ", ".join(paths) if paths else "No sufficiently relevant safe file was found yet"
    return (
        f"I created a read-only bounded plan for: {message.strip()}\n\n"
        f"Evidence inspected: {evidence}.\n"
        "No files have been changed and no commands have been run. A concrete patch must be previewed and approved separately."
    )


def _plan_action(message: str, context: dict) -> dict:
    paths = list(context["source_paths"][:10])
    return {
        "action_id": str(uuid4()), "action_type": "project_plan", "title": "Project work plan",
        "summary": "A bounded, non-mutating plan based only on cited project evidence.",
        "steps": ["Inspect the cited files", "Prepare a targeted immutable patch preview", "Request patch approval", "Propose separate validation commands"],
        "safety_information": {"files_modified": False, "commands_executed": False, "patch_approval_required": True, "command_approval_required": True},
        "status": "completed", "approval_required": False, "result_summary": "Planning completed without modifying the project.",
        "technical_details": {"project_plan": {
            "user_goal": message.strip(), "current_evidence": paths, "relevant_files": paths,
            "proposed_changes": ["Prepare the smallest evidence-backed patch after any additional inspection."],
            "likely_modified_files": paths[:5], "validations": ["Run the narrowest existing tests or checks after separate approval."],
            "risks": ["Project files are untrusted input.", "The request may require additional inspection before a safe patch."],
            "assumptions": [], "additional_inspection_needed": not bool(paths), "commands_require_approval": True,
            "patch_may_be_too_broad": len(paths) > 8, "source_paths": paths,
        }},
    }
