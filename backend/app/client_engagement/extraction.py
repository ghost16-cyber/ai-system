from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from backend.app.client_engagement.contracts import (
    ENGAGEMENT_SCHEMA_VERSION,
    Assumption,
    ClarificationQuestion,
    EngagementEvidenceReference,
    ExtractedRequirement,
    ModelExtractionResponse,
    QuestionPriority,
    RequirementClassification,
    RequirementSource,
    RequirementSourceKind,
    parse_strict_model_extraction,
)
from backend.app.client_engagement.limits import EngagementLimits, STAGE10_LIMITS


class ExtractionGateway(Protocol):
    def generate(self, prompt: str): ...


_SENTENCE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_DEADLINE = re.compile(r"\b(?:by|before|deadline|within)\s+(?:\w+\s+){0,4}(?:\d{1,2}(?:st|nd|rd|th)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month|day|20\d{2})\b", re.I)
_FRAMEWORK = re.compile(r"\b(react|vue|angular|svelte|next\.?js|django|flask|fastapi|rails|laravel|wordpress|python|typescript|javascript|java|\.net|postgres(?:ql)?|mysql|sqlite|mongodb)\b", re.I)
_PATH = re.compile(r"(?<!\w)(?:[A-Za-z0-9_.-]+[/\\])+[A-Za-z0-9_.-]+|\b(?:folder|directory|repository|repo|dataset|csv|logo|images?|menu)\b", re.I)
_EXCLUSION = re.compile(r"\b(?:without|do not|don't|must not|exclude|out of scope|no unrelated|unchanged)\b", re.I)
_NON_FUNCTIONAL = re.compile(r"\b(?:responsive|accessible|secure|performance|fast|compatible|compatibility|reliable|privacy|mobile|scalable)\b", re.I)
_FUNCTIONAL = re.compile(r"\b(?:form|upload|download|authentication|login|account|ordering|search|filter|page|chart|report|dashboard|api|database|menu)\b", re.I)
_DELIVERABLE = re.compile(r"\b(?:build|create|produce|deliver|include|add|fix|repair|diagnose|analy[sz]e|implement)\b", re.I)


def extract_requirements(
    evidence: Iterable[EngagementEvidenceReference | dict[str, Any]],
    *,
    model_gateway: ExtractionGateway | None = None,
    limits: EngagementLimits = STAGE10_LIMITS,
) -> tuple[list[ExtractedRequirement], list[str], dict[str, Any]]:
    """Run deterministic extraction first and use a bounded model only if needed."""
    items = [_as_evidence(item) for item in evidence]
    items.sort(key=lambda item: (item.collected_at, item.evidence_id))
    requirements = _deterministic(items, limits)
    audit = {"deterministic_count": len(requirements), "model_invoked": False, "model_rejected": False}
    assumptions: list[str] = []
    has_outcome = any(item.classification == RequirementClassification.OUTCOME for item in requirements)
    has_material = any(item.classification in {RequirementClassification.DELIVERABLE, RequirementClassification.FUNCTIONAL} for item in requirements)
    if model_gateway is not None and (not has_outcome or not has_material):
        audit["model_invoked"] = True
        selected = items[: min(len(items), 20)]
        payload = {
            "schema_version": "astra.client-engagement.extraction-request.v1",
            "instruction": "Extract only evidence-supported requirements. Keep assumptions separate.",
            "evidence": [{"evidence_id": item.evidence_id, "text": item.excerpt, "summary": item.structured_summary} for item in selected],
        }
        try:
            result = model_gateway.generate(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            raw = getattr(result, "raw_response", result)
            parsed = parse_model_requirements(str(raw), valid_evidence_ids={item.evidence_id for item in selected})
            requirements = _merge(requirements, _model_to_requirements(parsed), limits.max_requirements)
            assumptions = list(parsed.assumptions)[: limits.max_assumptions]
        except Exception:
            audit["model_rejected"] = True
    return requirements[: limits.max_requirements], assumptions, audit


def parse_model_requirements(raw: str, *, valid_evidence_ids: set[str]) -> ModelExtractionResponse:
    parsed = parse_strict_model_extraction(raw)
    for requirement in parsed.requirements:
        if not set(requirement.evidence_ids) <= valid_evidence_ids:
            raise ValueError("The model referenced evidence outside the selected engagement evidence.")
        if not requirement.explicit:
            raise ValueError("Model assumptions must not be returned as requirements.")
    return parsed


def generate_clarification_questions(
    *,
    engagement_id: str,
    requirements: Iterable[ExtractedRequirement | dict[str, Any]],
    evidence: Iterable[EngagementEvidenceReference | dict[str, Any]],
    prior_questions: Iterable[ClarificationQuestion | dict[str, Any]] = (),
    round_number: int = 1,
    limits: EngagementLimits = STAGE10_LIMITS,
) -> list[ClarificationQuestion]:
    values = [_as_requirement(item) for item in requirements]
    combined = " ".join(item.text for item in values).lower()
    prior_keys = {_question_key(item) for item in prior_questions}
    candidates: list[tuple[str, str, str, QuestionPriority, bool]] = []
    website = any(term in combined for term in ("website", "web site", "menu page", "contact form"))
    repair = any(term in combined for term in ("crash", "diagnose", "repair", "fix"))
    data_analysis = any(term in combined for term in ("dataset", "charts", "findings report", "analyze sales"))
    if website and not _answered(combined, ("deploy to", "hosting", "hosted on", "deployment environment")):
        candidates.append(("deployment_target", "Where should the finished website be delivered: locally verified files only, or a specific hosting environment?", "This changes delivery boundaries and deployment acceptance criteria.", QuestionPriority.BLOCKING, True))
    if "contact form" in combined and not _answered(combined, ("email address", "form service", "form delivery", "submission endpoint", "mailto")):
        candidates.append(("form_delivery", "How should contact-form submissions be delivered (for example, an existing form service or a supplied email/backend endpoint)?", "The answer determines data handling, dependencies, and testable form behavior.", QuestionPriority.BLOCKING, True))
    if repair and not _answered(combined, ("sample csv", "failing csv", "reproduction", "error output")):
        candidates.append(("repair_reproduction", "Is a representative failing CSV or reproducible error already present in the authorized project evidence?", "A reproducible failure materially improves diagnosis and acceptance testing.", QuestionPriority.HIGH, False))
    if data_analysis and not _answered(combined, ("audience", "decision maker", "executive", "technical audience")):
        candidates.append(("report_audience", "Who is the intended audience for the charts and short findings report?", "Audience affects chart labeling and the level of explanation, but Astra can use a general business audience assumption.", QuestionPriority.MEDIUM, False))
    deadline_known = any(item.classification == RequirementClassification.DEADLINE for item in values)
    if "deadline" in combined and not deadline_known:
        candidates.append(("deadline", "What exact delivery deadline should the scope use?", "A date is needed before timeline commitments can be assessed.", QuestionPriority.HIGH, True))
    ordered: list[ClarificationQuestion] = []
    now = datetime.now(timezone.utc)
    for semantic_key, question, rationale, priority, blocking in candidates:
        if semantic_key in prior_keys:
            continue
        identifier = _stable_id("question", engagement_id, semantic_key)
        ordered.append(ClarificationQuestion(
            schema_version=ENGAGEMENT_SCHEMA_VERSION, question_id=identifier,
            engagement_id=engagement_id, semantic_key=semantic_key, question=question,
            rationale=rationale, priority=priority, blocking=blocking,
            round_number=round_number, status="pending", created_at=now,
        ))
    ordered.sort(key=lambda item: ({QuestionPriority.BLOCKING: 0, QuestionPriority.HIGH: 1, QuestionPriority.MEDIUM: 2}[item.priority], item.semantic_key))
    return ordered[: limits.max_questions_per_round]


def reasonable_assumption_for(question: ClarificationQuestion | dict[str, Any]) -> Assumption:
    value = question if isinstance(question, ClarificationQuestion) else ClarificationQuestion.model_validate(question)
    defaults = {
        "deployment_target": "Delivery is limited to locally verified project files; external deployment is excluded.",
        "form_delivery": "The contact form will use a documented integration placeholder; no messages are sent and no credentials are added.",
        "repair_reproduction": "Astra will derive a bounded reproduction from authorized project evidence before proposing a repair.",
        "report_audience": "Charts and findings will target a general business audience with plain-language labels.",
        "deadline": "No guaranteed delivery date is included; effort is expressed only as bounded work units.",
    }
    text = defaults.get(value.semantic_key, f"A conservative implementation boundary will be used for: {value.question}")
    return Assumption(
        schema_version=ENGAGEMENT_SCHEMA_VERSION,
        assumption_id=_stable_id("assumption", value.engagement_id, value.question_id),
        text=text, evidence_ids=[], accepted_by_user=True,
        materially_reduces_confidence=value.blocking, created_at=datetime.now(timezone.utc),
    )


def _deterministic(evidence: list[EngagementEvidenceReference], limits: EngagementLimits) -> list[ExtractedRequirement]:
    values: list[ExtractedRequirement] = []
    for item in evidence:
        text = (item.excerpt or "").strip()
        if not text:
            continue
        sentences = [part.strip(" \t-•") for part in _SENTENCE.split(text) if part.strip()]
        for sentence in sentences:
            classifications: list[RequirementClassification] = []
            if not values and item.source_type.value == "original_chat_request":
                classifications.append(RequirementClassification.OUTCOME)
            if _DELIVERABLE.search(sentence): classifications.append(RequirementClassification.DELIVERABLE)
            if _FUNCTIONAL.search(sentence): classifications.append(RequirementClassification.FUNCTIONAL)
            if _NON_FUNCTIONAL.search(sentence): classifications.append(RequirementClassification.NON_FUNCTIONAL)
            if _FRAMEWORK.search(sentence): classifications.append(RequirementClassification.PLATFORM_CONSTRAINT)
            if _PATH.search(sentence): classifications.append(RequirementClassification.FILE_REFERENCE)
            if _DEADLINE.search(sentence): classifications.append(RequirementClassification.DEADLINE)
            if _EXCLUSION.search(sentence): classifications.append(RequirementClassification.EXCLUSION)
            if re.search(r"\b(?:must|should|need|accept|verified|pass)\b", sentence, re.I): classifications.append(RequirementClassification.ACCEPTANCE_SIGNAL)
            if not classifications:
                classifications = [RequirementClassification.UNKNOWN]
            for classification in dict.fromkeys(classifications):
                values.append(_requirement(sentence, classification, item.evidence_id, item.source_type.value == "clarification_answer"))
            if len(values) >= limits.max_requirements:
                return _normalize(values, limits.max_requirements)
    combined = " ".join((item.excerpt or "") for item in evidence).lower()
    source_id = next((item.evidence_id for item in evidence if item.excerpt), "")
    extras: list[tuple[str, RequirementClassification]] = []
    if any(term in combined for term in ("crash", "diagnose and fix", "repair")):
        extras.extend([
            ("Reproduce the reported failure using authorized project evidence.", RequirementClassification.DELIVERABLE),
            ("Diagnose the root cause before preparing a change.", RequirementClassification.DELIVERABLE),
            ("Prepare a bounded repair for the confirmed cause.", RequirementClassification.DELIVERABLE),
            ("Run regression tests and verify the repaired behavior.", RequirementClassification.DELIVERABLE),
        ])
    if "no unrelated" in combined or "without changing unrelated" in combined:
        extras.append(("Do not change unrelated features or files.", RequirementClassification.TECHNICAL_CONSTRAINT))
    if "four chart" in combined or "4 chart" in combined:
        extras.append(("Produce exactly four evidence-grounded charts.", RequirementClassification.DELIVERABLE))
    if "findings report" in combined:
        extras.append(("Produce a short findings report without inventing conclusions before analysis.", RequirementClassification.DELIVERABLE))
    for text, classification in extras:
        if source_id:
            values.append(_requirement(text, classification, source_id, False))
    return _normalize(values, limits.max_requirements)


def _requirement(text: str, classification: RequirementClassification, evidence_id: str, from_answer: bool) -> ExtractedRequirement:
    normalized = re.sub(r"\s+", " ", text).strip()[:1200]
    identifier = _stable_id("requirement", classification.value, normalized.lower())
    return ExtractedRequirement(
        schema_version=ENGAGEMENT_SCHEMA_VERSION, requirement_id=identifier,
        text=normalized, classification=classification,
        source=RequirementSource(
            schema_version=ENGAGEMENT_SCHEMA_VERSION,
            kind=RequirementSourceKind.USER_ANSWER if from_answer else RequirementSourceKind.EXPLICIT_EVIDENCE,
            evidence_ids=[evidence_id],
        ), explicit=True, material=classification != RequirementClassification.UNKNOWN,
        confidence=0.98 if from_answer else 0.94,
    )


def _model_to_requirements(parsed: ModelExtractionResponse) -> list[ExtractedRequirement]:
    return [ExtractedRequirement(
        schema_version=ENGAGEMENT_SCHEMA_VERSION,
        requirement_id=_stable_id("requirement", item.classification.value, item.text.lower()),
        text=item.text, classification=item.classification,
        source=RequirementSource(schema_version=ENGAGEMENT_SCHEMA_VERSION, kind=RequirementSourceKind.EXPLICIT_EVIDENCE, evidence_ids=item.evidence_ids),
        explicit=True, material=item.classification != RequirementClassification.UNKNOWN, confidence=0.72,
    ) for item in parsed.requirements]


def _merge(first: list[ExtractedRequirement], second: list[ExtractedRequirement], limit: int) -> list[ExtractedRequirement]:
    return _normalize([*first, *second], limit)


def _normalize(values: list[ExtractedRequirement], limit: int) -> list[ExtractedRequirement]:
    unique: dict[tuple[str, str], ExtractedRequirement] = {}
    for value in values:
        key = (value.classification.value, re.sub(r"\W+", " ", value.text.lower()).strip())
        if key not in unique:
            unique[key] = value
    return sorted(unique.values(), key=lambda item: (item.classification.value, item.requirement_id))[:limit]


def _as_evidence(value: EngagementEvidenceReference | dict[str, Any]) -> EngagementEvidenceReference:
    return value if isinstance(value, EngagementEvidenceReference) else EngagementEvidenceReference.model_validate(value)


def _as_requirement(value: ExtractedRequirement | dict[str, Any]) -> ExtractedRequirement:
    return value if isinstance(value, ExtractedRequirement) else ExtractedRequirement.model_validate(value)


def _question_key(value: ClarificationQuestion | dict[str, Any]) -> str:
    return value.semantic_key if isinstance(value, ClarificationQuestion) else str(value.get("semantic_key") or "")


def _answered(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]


__all__ = ["extract_requirements", "generate_clarification_questions", "parse_model_requirements", "reasonable_assumption_for"]
