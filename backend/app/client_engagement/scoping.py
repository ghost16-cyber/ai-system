from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from backend.app.client_engagement.contracts import (
    ENGAGEMENT_SCHEMA_VERSION,
    AcceptanceCriterion,
    ApprovalStatus,
    Assumption,
    ClarificationQuestion,
    Constraint,
    CreatorType,
    Deliverable,
    Dependency,
    EngagementEvidenceReference,
    Exclusion,
    ExtractedRequirement,
    Milestone,
    RequirementClassification,
    ReviewMode,
    Risk,
    ScopeChangeClassification,
    ScopeProposal,
    ScopeRevision,
)
from backend.app.client_engagement.estimation import estimate_effort
from backend.app.client_engagement.limits import EngagementLimits, STAGE10_LIMITS


def build_scope_revision(
    *,
    engagement_id: str,
    requirements: Iterable[ExtractedRequirement | dict[str, Any]],
    evidence: Iterable[EngagementEvidenceReference | dict[str, Any]],
    assumptions: Iterable[Assumption | dict[str, Any]] = (),
    questions: Iterable[ClarificationQuestion | dict[str, Any]] = (),
    revision_number: int = 1,
    parent_revision_id: str | None = None,
    reason: str = "Initial evidence-grounded scope",
    creator_type: CreatorType = CreatorType.DETERMINISTIC_SYSTEM,
    prior_scope: ScopeProposal | dict[str, Any] | None = None,
    requested_change: str | None = None,
    limits: EngagementLimits = STAGE10_LIMITS,
) -> ScopeRevision:
    reqs = [_req(item) for item in requirements]
    evidence_items = [_evidence(item) for item in evidence]
    assumption_values = [_assumption(item) for item in assumptions][: limits.max_assumptions]
    question_values = [_question(item) for item in questions]
    if prior_scope is not None and requested_change:
        scope = _scope_with_change(
            prior_scope if isinstance(prior_scope, ScopeProposal) else ScopeProposal.model_validate(prior_scope),
            requested_change=requested_change, evidence=evidence_items, limits=limits,
        )
    else:
        scope = _build_scope(reqs, evidence_items, assumption_values, question_values, limits)
    canonical = canonical_scope_serialization(scope)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    evidence_hashes = {item.evidence_id: item.content_hash or "" for item in evidence_items if item.content_hash}
    return ScopeRevision(
        schema_version=ENGAGEMENT_SCHEMA_VERSION, revision_id=uuid4().hex,
        engagement_id=engagement_id, revision_number=revision_number,
        scope=scope, canonical_scope=canonical, scope_hash=digest,
        source_evidence_hashes=dict(sorted(evidence_hashes.items())),
        parent_revision_id=parent_revision_id, reason=reason,
        creator_type=creator_type, approval_status=ApprovalStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )


def canonical_scope_serialization(scope: ScopeProposal | dict[str, Any]) -> str:
    value = scope if isinstance(scope, ScopeProposal) else ScopeProposal.model_validate(scope)
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_scope_revision(revision: ScopeRevision | dict[str, Any]) -> ScopeRevision:
    value = revision if isinstance(revision, ScopeRevision) else ScopeRevision.model_validate(revision)
    canonical = canonical_scope_serialization(value.scope)
    if value.canonical_scope != canonical:
        raise ValueError("The canonical scope representation does not match the stored proposal.")
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != value.scope_hash:
        raise ValueError("The scope hash does not match the canonical proposal.")
    return value


def classify_scope_change(text: str) -> ScopeChangeClassification:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if re.search(r"\b(cancel|stop|terminate)\b", normalized): return ScopeChangeClassification.CANCELLATION
    if re.search(r"\b(remove|drop|exclude|no longer)\b", normalized): return ScopeChangeClassification.REMOVAL
    if re.search(r"\b(framework|database|platform|deadline|target|compatib|security constraint)\b", normalized): return ScopeChangeClassification.CONSTRAINT
    if re.search(r"\b(acceptance|criterion|criteria|definition of done)\b", normalized): return ScopeChangeClassification.ACCEPTANCE
    if re.search(r"\b(add|include|support|also|another|account|ordering|authentication)\b", normalized): return ScopeChangeClassification.ADDITION
    if re.search(r"\b(typo|wording|rename|correct)\b", normalized): return ScopeChangeClassification.NON_MATERIAL
    return ScopeChangeClassification.CLARIFICATION


def scope_change_impact(scope: ScopeProposal | dict[str, Any], requested_change: str) -> dict[str, Any]:
    value = scope if isinstance(scope, ScopeProposal) else ScopeProposal.model_validate(scope)
    classification = classify_scope_change(requested_change)
    tokens = set(re.findall(r"[a-z0-9]+", requested_change.lower()))
    affected = [item.deliverable_id for item in value.deliverables if tokens & set(re.findall(r"[a-z0-9]+", (item.title + " " + item.description).lower()))]
    if not affected and classification in {ScopeChangeClassification.ADDITION, ScopeChangeClassification.CONSTRAINT, ScopeChangeClassification.ACCEPTANCE}:
        affected = [item.deliverable_id for item in value.deliverables]
    milestones = [item.milestone_id for item in value.milestones if set(item.deliverable_ids) & set(affected)]
    material = classification not in {ScopeChangeClassification.CLARIFICATION, ScopeChangeClassification.NON_MATERIAL}
    return {
        "classification": classification,
        "affected_deliverable_ids": affected,
        "affected_milestone_ids": milestones,
        "estimate_impact": "Expected work units and uncertainty increase and must be recalculated." if classification == ScopeChangeClassification.ADDITION else "The estimate must be recalculated against the revised scope.",
        "risk_impact": "Architecture, data handling, security, and integration risks require fresh review." if material else "No material risk change is currently identified.",
        "acceptance_criteria_impact": "New or changed deliverables require corresponding testable acceptance criteria." if material else "Acceptance wording will be regenerated if the canonical scope changes.",
    }


def _build_scope(reqs: list[ExtractedRequirement], evidence: list[EngagementEvidenceReference], assumptions: list[Assumption], questions: list[ClarificationQuestion], limits: EngagementLimits) -> ScopeProposal:
    explicit = [item for item in reqs if item.explicit]
    combined = " ".join(item.text for item in explicit).lower()
    outcome = next((item.text for item in explicit if item.classification == RequirementClassification.OUTCOME), explicit[0].text if explicit else "Complete the requested project outcome.")
    deliverable_specs = _deliverable_specs(combined, explicit)
    deliverables = [_deliverable(index, title, description, evidence_ids, criteria, limits) for index, (title, description, evidence_ids, criteria) in enumerate(deliverable_specs[: limits.max_deliverables], start=1)]
    if not deliverables:
        evidence_ids = _all_evidence_ids(explicit)
        deliverables = [_deliverable(1, "Requested project outcome", outcome, evidence_ids, ["The approved outcome is implemented and verified against the authorized project evidence."], limits)]
    constraints = [Constraint(schema_version=ENGAGEMENT_SCHEMA_VERSION, constraint_id=f"constraint-{index:02d}", text=item.text, evidence_ids=item.source.evidence_ids) for index, item in enumerate(explicit, start=1) if item.classification in {RequirementClassification.TECHNICAL_CONSTRAINT, RequirementClassification.PLATFORM_CONSTRAINT, RequirementClassification.DEADLINE}][:30]
    exclusions = [Exclusion(schema_version=ENGAGEMENT_SCHEMA_VERSION, exclusion_id=f"exclusion-{index:02d}", text=item.text, evidence_ids=item.source.evidence_ids) for index, item in enumerate(explicit, start=1) if item.classification == RequirementClassification.EXCLUSION][:30]
    if "contact form" in combined and not any("external" in item.text.lower() for item in exclusions):
        exclusions.append(Exclusion(schema_version=ENGAGEMENT_SCHEMA_VERSION, exclusion_id="exclusion-external-send", text="External message sending is excluded until a separately approved delivery integration is supplied.", evidence_ids=[]))
    if not any("deploy" in item.text.lower() for item in explicit):
        exclusions.append(Exclusion(schema_version=ENGAGEMENT_SCHEMA_VERSION, exclusion_id="exclusion-deployment", text="External deployment, purchases, and production resource changes are excluded.", evidence_ids=[]))
    dependencies: list[Dependency] = []
    if any(item.source_type.value == "authorized_project_folder" for item in evidence):
        dependencies.append(Dependency(schema_version=ENGAGEMENT_SCHEMA_VERSION, dependency_id="dependency-authorized-folder", text="Continued access to the explicitly authorized project folder and its current evidence.", owner="client", evidence_ids=[item.evidence_id for item in evidence if item.source_type.value == "authorized_project_folder"][:20]))
    if "contact form" in combined:
        dependencies.append(Dependency(schema_version=ENGAGEMENT_SCHEMA_VERSION, dependency_id="dependency-form-delivery", text="A client-approved form-delivery endpoint or service is required for live message delivery.", owner="client", evidence_ids=[]))
    risks = _risks(combined, assumptions)
    milestones = [Milestone(schema_version=ENGAGEMENT_SCHEMA_VERSION, milestone_id=f"milestone-{index:02d}", title=item.title, deliverable_ids=[item.deliverable_id], completion_signal=f"All acceptance criteria for {item.title} have recorded evidence.") for index, item in enumerate(deliverables, start=1)][: limits.max_milestones]
    repo_count = sum(1 for item in evidence if item.source_type.value == "authorized_project_folder")
    estimate = estimate_effort(deliverables=deliverables, dependencies=dependencies, risks=risks, repository_file_count=repo_count, testing_requirement_count=sum(len(item.acceptance_criteria) for item in deliverables), assumptions=[item.text for item in assumptions], limits=limits)
    traceability = {item.deliverable_id: sorted(set(item.evidence_ids + [value for criterion in item.acceptance_criteria for value in criterion.evidence_ids])) for item in deliverables}
    return ScopeProposal(
        schema_version=ENGAGEMENT_SCHEMA_VERSION, engagement_title=_title(outcome),
        problem_statement=outcome, desired_outcome=outcome,
        deliverables=deliverables,
        functional_requirements=[item for item in explicit if item.classification == RequirementClassification.FUNCTIONAL],
        non_functional_requirements=[item for item in explicit if item.classification == RequirementClassification.NON_FUNCTIONAL],
        constraints=constraints, dependencies=dependencies,
        client_responsibilities=["Provide and maintain access to explicitly authorized evidence.", "Answer material clarification questions or explicitly accept documented assumptions.", "Perform human-review acceptance checks where identified."],
        astra_responsibilities=["Use only authorized evidence and preserve traceability.", "Prepare bounded changes through the existing project-delivery approval gates.", "Record independent verification evidence for each acceptance criterion."],
        assumptions=assumptions, exclusions=exclusions[:30], milestones=milestones,
        risks=risks, open_questions=[item for item in questions if item.status == "pending"],
        effort_estimate=estimate,
        recommended_delivery_configuration={"workflow": "stage9_project_delivery", "patch_approval_required": True, "command_approval_required": True, "max_work_units": min(20, estimate.pessimistic.maximum), "scope_hash_required": True},
        evidence_traceability=traceability,
    )


def _deliverable_specs(combined: str, reqs: list[ExtractedRequirement]) -> list[tuple[str, str, list[str], list[str]]]:
    source_ids = _all_evidence_ids(reqs)
    values: list[tuple[str, str, list[str], list[str]]] = []
    if "website" in combined or "web site" in combined:
        values.append(("Responsive website", "A responsive website using only supplied, authorized brand and content evidence.", source_ids, ["The site renders without horizontal overflow at common mobile and desktop widths.", "Authorized logo and image assets are referenced without exposing local filesystem paths."]))
    if "menu page" in combined or "view the menu" in combined:
        values.append(("Menu page", "A dedicated page where customers can view the supplied menu.", source_ids, ["The menu page is reachable through site navigation.", "Displayed menu content is traceable to authorized evidence and remains readable on mobile and desktop."]))
    if "contact form" in combined:
        values.append(("Contact form", "A contact form with documented validation and a bounded delivery integration.", source_ids, ["Required fields reject empty or invalid submissions with accessible feedback.", "A test submission follows the approved delivery behavior without exposing secrets or sending unintended external messages."]))
    if any(term in combined for term in ("crash", "diagnose and fix", "repair")):
        values.extend([
            ("Failure reproduction and diagnosis", "A reproducible CSV-upload failure and an evidence-backed root-cause diagnosis.", source_ids, ["The original failure is reproduced with bounded evidence or explicitly documented as not reproducible.", "The diagnosis identifies the affected code path and supporting evidence."]),
            ("Bounded repair", "A minimal repair that preserves unrelated behavior.", source_ids, ["The confirmed CSV-upload failure no longer occurs for the reproducing input.", "The patch contains no unrelated file or feature changes."]),
            ("Regression verification", "Regression tests and verification for the repaired upload behavior.", source_ids, ["A regression test fails before the repair or otherwise demonstrates the original defect.", "The separately approved validation passes after the repair and existing relevant tests remain green."]),
        ])
    if "four chart" in combined or "4 chart" in combined:
        values.append(("Four analytical charts", "Exactly four charts derived from the supplied sales dataset.", source_ids, ["Exactly four charts are produced from authorized dataset evidence.", "Each chart has a descriptive title, labeled measures, and a traceable data basis."]))
    if "findings report" in combined:
        values.append(("Short findings report", "A concise report summarizing only conclusions supported by the completed analysis.", source_ids, ["The report references the four produced charts and their measured evidence.", "No business conclusion is stated without support from the analyzed dataset."]))
    mentioned = [item for item in reqs if item.classification == RequirementClassification.DELIVERABLE]
    if not values:
        for item in mentioned[:20]:
            values.append((_title(item.text), item.text, item.source.evidence_ids, [f"The deliverable is present and satisfies the approved requirement: {item.text}"]))
    return _dedupe_specs(values)


def _deliverable(index: int, title: str, description: str, evidence_ids: list[str], criteria: list[str], limits: EngagementLimits) -> Deliverable:
    deliverable_id = f"deliverable-{index:02d}"
    accepted = [AcceptanceCriterion(schema_version=ENGAGEMENT_SCHEMA_VERSION, criterion_id=f"criterion-{index:02d}-{offset:02d}", deliverable_id=deliverable_id, statement=text, review_mode=ReviewMode.HUMAN if "readable" in text.lower() or "descriptive" in text.lower() else ReviewMode.AUTOMATED, evidence_ids=evidence_ids[:20]) for offset, text in enumerate(criteria[: limits.max_acceptance_criteria_per_deliverable], start=1)]
    return Deliverable(schema_version=ENGAGEMENT_SCHEMA_VERSION, deliverable_id=deliverable_id, title=title, description=description, evidence_ids=evidence_ids[:20], acceptance_criteria=accepted)


def _risks(combined: str, assumptions: list[Assumption]) -> list[Risk]:
    values: list[Risk] = []
    if "contact form" in combined or "account" in combined or "ordering" in combined:
        values.append(Risk(schema_version=ENGAGEMENT_SCHEMA_VERSION, risk_id="risk-data-handling", description="User data handling and external integration details may be incomplete.", likelihood="medium", impact="high", mitigation="Keep delivery local and inert until endpoints, retention, security, and credentials receive separate approval.", evidence_ids=[]))
    if assumptions:
        values.append(Risk(schema_version=ENGAGEMENT_SCHEMA_VERSION, risk_id="risk-assumptions", description="Accepted assumptions may differ from final client preferences.", likelihood="medium", impact="medium", mitigation="Keep assumptions visible and require a new immutable revision for material changes.", evidence_ids=[]))
    return values


def _scope_with_change(prior: ScopeProposal, *, requested_change: str, evidence: list[EngagementEvidenceReference], limits: EngagementLimits) -> ScopeProposal:
    classification = classify_scope_change(requested_change)
    payload = prior.model_dump(mode="json")
    source_ids = [item.evidence_id for item in evidence if item.source_type.value in {"clarification_answer", "original_chat_request"}][-5:]
    if classification == ScopeChangeClassification.ADDITION:
        title = _title(re.sub(r"^(?:please\s+)?(?:add|include|also)\s+", "", requested_change, flags=re.I))
        next_index = len(prior.deliverables) + 1
        criteria = [f"The added capability is implemented according to the approved change: {requested_change}", "Security, data handling, and regression evidence is recorded for the added capability."]
        added = _deliverable(next_index, title, requested_change, source_ids, criteria, limits)
        payload["deliverables"] = [*payload["deliverables"], added.model_dump(mode="json")]
        payload["milestones"] = [*payload["milestones"], Milestone(schema_version=ENGAGEMENT_SCHEMA_VERSION, milestone_id=f"milestone-{next_index:02d}", title=title, deliverable_ids=[added.deliverable_id], completion_signal=f"All acceptance criteria for {title} have recorded evidence.").model_dump(mode="json")]
        payload["risks"] = [*payload["risks"], Risk(schema_version=ENGAGEMENT_SCHEMA_VERSION, risk_id=f"risk-scope-change-{next_index:02d}", description="The material addition may change architecture, data handling, security, and integration boundaries.", likelihood="medium", impact="high", mitigation="Replan through Stage 9 only after exact approval of this revision.", evidence_ids=source_ids).model_dump(mode="json")]
    elif classification == ScopeChangeClassification.REMOVAL:
        tokens = set(re.findall(r"[a-z0-9]+", requested_change.lower()))
        remaining = [item for item in prior.deliverables if not tokens & set(re.findall(r"[a-z0-9]+", item.title.lower()))]
        if remaining:
            payload["deliverables"] = [item.model_dump(mode="json") for item in remaining]
            keep = {item.deliverable_id for item in remaining}
            payload["milestones"] = [item.model_dump(mode="json") for item in prior.milestones if set(item.deliverable_ids) & keep]
    else:
        payload["constraints"] = [*payload["constraints"], Constraint(schema_version=ENGAGEMENT_SCHEMA_VERSION, constraint_id=f"constraint-change-{len(prior.constraints)+1:02d}", text=requested_change, evidence_ids=source_ids).model_dump(mode="json")]
    interim = ScopeProposal.model_validate(payload)
    estimate = estimate_effort(deliverables=interim.deliverables, dependencies=interim.dependencies, risks=interim.risks, testing_requirement_count=sum(len(item.acceptance_criteria) for item in interim.deliverables), assumptions=[item.text for item in interim.assumptions], limits=limits)
    payload["effort_estimate"] = estimate.model_dump(mode="json")
    payload["recommended_delivery_configuration"]["max_work_units"] = min(20, estimate.pessimistic.maximum)
    payload["evidence_traceability"] = {item.deliverable_id: sorted(set(item.evidence_ids + [value for criterion in item.acceptance_criteria for value in criterion.evidence_ids])) for item in interim.deliverables}
    return ScopeProposal.model_validate(payload)


def _dedupe_specs(values):
    seen = set(); output = []
    for value in values:
        key = value[0].lower()
        if key not in seen: seen.add(key); output.append(value)
    return output


def _all_evidence_ids(reqs: list[ExtractedRequirement]) -> list[str]:
    return list(dict.fromkeys(value for item in reqs for value in item.source.evidence_ids))[:20]


def _title(text: str) -> str:
    value = re.sub(r"\s+", " ", text.strip()).rstrip(".")
    return (value[:80] + ("…" if len(value) > 80 else "")) or "Client engagement"


def _req(value): return value if isinstance(value, ExtractedRequirement) else ExtractedRequirement.model_validate(value)
def _evidence(value): return value if isinstance(value, EngagementEvidenceReference) else EngagementEvidenceReference.model_validate(value)
def _assumption(value): return value if isinstance(value, Assumption) else Assumption.model_validate(value)
def _question(value): return value if isinstance(value, ClarificationQuestion) else ClarificationQuestion.model_validate(value)


__all__ = ["build_scope_revision", "canonical_scope_serialization", "classify_scope_change", "scope_change_impact", "verify_scope_revision"]
