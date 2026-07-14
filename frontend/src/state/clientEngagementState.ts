export type EngagementStatus =
  | "draft" | "collecting_evidence" | "extracting_requirements"
  | "clarification_required" | "scope_preparing" | "scope_ready"
  | "awaiting_scope_approval" | "scope_approved" | "project_launching"
  | "project_launched" | "scope_change_requested" | "scope_change_review"
  | "cancelled" | "failed";

export interface EngagementQuestion {
  id: string;
  semanticKey: string;
  question: string;
  rationale: string;
  blocking: boolean;
  priority: string;
}

export interface EngagementCriterion {
  id: string;
  statement: string;
  reviewMode: string;
}

export interface EngagementDeliverable {
  id: string;
  title: string;
  description: string;
  criteria: EngagementCriterion[];
}

export interface EngagementScope {
  revisionId: string;
  revisionNumber: number;
  scopeHash: string;
  title: string;
  objective: string;
  problemStatement: string;
  deliverables: EngagementDeliverable[];
  functionalRequirements: string[];
  nonFunctionalRequirements: string[];
  milestones: Array<{ title: string; completionSignal: string }>;
  assumptions: string[];
  exclusions: string[];
  risks: string[];
  clientResponsibilities: string[];
  astraResponsibilities: string[];
  estimate?: {
    relativeSize: string;
    workUnits: number;
    expected: string;
    pessimistic: string;
    confidence: string;
    uncertainties: string[];
  };
}

export interface ClientEngagementAction {
  engagementId: string;
  conversationId: string;
  status: EngagementStatus;
  stateVersion: number;
  outcome: string;
  evidence: Array<{ id: string; type: string; label: string; stale: boolean }>;
  missingInformation: string[];
  questions: EngagementQuestion[];
  scope?: EngagementScope;
  approvedRevisionId?: string;
  launch?: { deliveryJobId: string; scopeRevisionId: string };
  scopeChanges: Array<{ classification: string; requestedChange: string; estimateImpact: string; riskImpact: string; revisionId?: string }>;
  limitation?: string;
  technical: Record<string, unknown>;
}

const statuses = new Set<EngagementStatus>([
  "draft", "collecting_evidence", "extracting_requirements", "clarification_required",
  "scope_preparing", "scope_ready", "awaiting_scope_approval", "scope_approved",
  "project_launching", "project_launched", "scope_change_requested", "scope_change_review",
  "cancelled", "failed",
]);

export function clientEngagementActionFromPayload(payload: unknown): ClientEngagementAction | null {
  const action = object(payload);
  if (action.action_type !== "client_engagement") return null;
  const publicValue = object(object(action.technical_details).client_engagement);
  const engagementId = string(publicValue.engagement_id);
  const status = string(publicValue.state) as EngagementStatus;
  if (!engagementId || !statuses.has(status)) return null;
  const revision = object(publicValue.current_scope_revision);
  const proposal = object(revision.scope);
  const estimate = object(proposal.effort_estimate);
  const expected = object(estimate.expected);
  const pessimistic = object(estimate.pessimistic);
  const scope = string(revision.revision_id) ? {
    revisionId: string(revision.revision_id), revisionNumber: number(revision.revision_number, 1),
    scopeHash: string(revision.scope_hash), title: string(proposal.engagement_title),
    objective: string(proposal.desired_outcome), problemStatement: string(proposal.problem_statement),
    deliverables: objects(proposal.deliverables).map((item) => ({
      id: string(item.deliverable_id), title: string(item.title), description: string(item.description),
      criteria: objects(item.acceptance_criteria).map((criterion) => ({ id: string(criterion.criterion_id), statement: string(criterion.statement), reviewMode: string(criterion.review_mode) })),
    })),
    functionalRequirements: objects(proposal.functional_requirements).map((item) => string(item.text)).filter(Boolean),
    nonFunctionalRequirements: objects(proposal.non_functional_requirements).map((item) => string(item.text)).filter(Boolean),
    milestones: objects(proposal.milestones).map((item) => ({ title: string(item.title), completionSignal: string(item.completion_signal) })),
    assumptions: objects(proposal.assumptions).map((item) => string(item.text)).filter(Boolean),
    exclusions: objects(proposal.exclusions).map((item) => string(item.text)).filter(Boolean),
    risks: objects(proposal.risks).map((item) => string(item.description)).filter(Boolean),
    clientResponsibilities: strings(proposal.client_responsibilities), astraResponsibilities: strings(proposal.astra_responsibilities),
    estimate: Object.keys(estimate).length ? {
      relativeSize: string(estimate.relative_size), workUnits: number(estimate.estimated_work_unit_count),
      expected: `${number(expected.minimum)}–${number(expected.maximum)} work units`,
      pessimistic: `${number(pessimistic.minimum)}–${number(pessimistic.maximum)} work units`,
      confidence: string(estimate.confidence), uncertainties: strings(estimate.uncertainty_drivers),
    } : undefined,
  } : undefined;
  const launch = object(publicValue.project_launch);
  return {
    engagementId, conversationId: string(publicValue.conversation_id), status,
    stateVersion: number(publicValue.state_version), outcome: string(publicValue.understood_outcome),
    evidence: objects(publicValue.authorized_evidence).map((item) => ({ id: string(item.evidence_id), type: string(item.source_type), label: string(item.label), stale: item.is_stale === true })),
    missingInformation: strings(publicValue.missing_information),
    questions: objects(publicValue.pending_questions).map((item) => ({ id: string(item.question_id), semanticKey: string(item.semantic_key), question: string(item.question), rationale: string(item.rationale), blocking: item.blocking === true, priority: string(item.priority) })).filter((item) => item.id && item.question),
    scope, approvedRevisionId: string(publicValue.approved_scope_revision_id) || undefined,
    launch: string(launch.delivery_job_id) ? { deliveryJobId: string(launch.delivery_job_id), scopeRevisionId: string(launch.scope_revision_id) } : undefined,
    scopeChanges: objects(publicValue.scope_changes).map((item) => ({ classification: string(item.classification), requestedChange: string(item.requested_change), estimateImpact: string(item.estimate_impact), riskImpact: string(item.risk_impact), revisionId: string(item.resulting_revision_id) || undefined })),
    limitation: string(publicValue.limitation) || undefined, technical: publicValue,
  };
}

export function exactScopeApprovalRequest(action: ClientEngagementAction) {
  if (!action.scope || action.limitation || !["awaiting_scope_approval", "scope_change_review"].includes(action.status)) return null;
  return { conversation_id: action.conversationId, expected_state_version: action.stateVersion, revision_id: action.scope.revisionId, scope_hash: action.scope.scopeHash };
}

export function mergeClientEngagementAction(current: ClientEngagementAction | undefined, incoming: ClientEngagementAction | null) {
  if (!incoming) return current;
  return !current || current.engagementId === incoming.engagementId ? incoming : current;
}

function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function objects(value: unknown): Array<Record<string, unknown>> { return Array.isArray(value) ? value.map(object).filter((item) => Object.keys(item).length) : []; }
function string(value: unknown): string { return typeof value === "string" ? value : ""; }
function number(value: unknown, fallback = 0): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 100) : []; }
