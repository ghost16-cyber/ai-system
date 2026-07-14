export type ProjectJobStatus =
  | "intake"
  | "needs_clarification"
  | "planned"
  | "patch_proposed"
  | "patch_approved"
  | "implementing"
  | "validating"
  | "blocked"
  | "completed"
  | "cancelled";

export interface ProjectJobAction {
  jobId: string;
  status: ProjectJobStatus;
  objective: string;
  deliverables: string[];
  constraints: string[];
  acceptanceCriteria: string[];
  relevantPaths: string[];
  missingInformation: string[];
  risks: string[];
  clarification?: { question?: string; answer?: string };
  plan: {
    findings: Array<{ claim: string; relativePath: string }>;
    files: string[];
    steps: string[];
    safetyImpact: string;
    assumptions: string[];
  };
  analysis: {
    status: string;
    findings: Array<{ relativePath: string; summary: string; parseStatus: string }>;
    symbols: Array<{ relativePath: string; name: string; kind: string; startLine?: number; endLine?: number }>;
    coherentFiles: Array<{ relativePath: string; classification: string; reason: string }>;
    impactedTests: string[];
    confidence: "high" | "medium" | "low" | "unknown";
    warnings: string[];
    planOnly: boolean;
    planOnlyReasons: string[];
    prevalidation: { status: string; checks: string[]; warnings: string[] };
  };
  synthesis: {
    status: string;
    strategy?: string;
    provider?: string;
    model?: string;
    contractVersion?: string;
    evidence: { fileCount: number; excerptCount: number; excerptChars: number };
    confidence: "high" | "medium" | "low" | "unknown";
    confidenceReasons: string[];
    modelClaim?: string;
    warnings: string[];
    assumptions: string[];
    requiresClarification: boolean;
    summary?: string;
  };
  repair: {
    status: string;
    chainId?: string;
    cycleId?: string;
    cycleNumber: number;
    maxCycles: number;
    failureEvidenceId?: string;
    diagnosisId?: string;
    parentPatchId?: string;
    repairPatchId?: string;
    commandExecutionId?: string;
    strategy?: string;
    provider?: string;
    model?: string;
    confidence: "high" | "medium" | "low" | "unknown";
    confidenceReasons: string[];
    rootCauses: Array<{ reasonCode: string; explanation: string; affectedFiles: string[]; affectedSymbols: string[] }>;
    affectedFiles: string[];
    affectedSymbols: string[];
    assumptions: string[];
    warnings: string[];
    clarification?: { question?: string; answer?: string };
    failedCommandSummary?: string;
    outputTruncated: boolean;
    redactionCount: number;
    validationRerunStatus: string;
    rollbackAvailable: boolean;
  };
  patchIds: string[];
  commandPlanIds: string[];
  validationPlan: Array<Record<string, unknown>>;
  validationResults: Array<Record<string, unknown>>;
  completionSummary?: Record<string, unknown>;
  revisionCount: number;
  maxRevisionCycles: number;
  technical: Record<string, unknown>;
}

const statuses = new Set<ProjectJobStatus>([
  "intake", "needs_clarification", "planned", "patch_proposed", "patch_approved",
  "implementing", "validating", "blocked", "completed", "cancelled",
]);

export function projectJobActionFromPayload(payload: unknown): ProjectJobAction | null {
  const action = asObject(payload);
  if (action.action_type !== "project_job") return null;
  const details = asObject(action.technical_details);
  const job = asObject(details.project_job);
  const jobId = readString(job.job_id);
  const rawStatus = readString(job.status) as ProjectJobStatus;
  if (!jobId || !statuses.has(rawStatus)) return null;
  const plan = asObject(job.implementation_plan);
  const analysis = asObject(job.analysis);
  const confidence = asObject(analysis.confidence);
  const prevalidation = asObject(analysis.prevalidation);
  const synthesis = asObject(job.synthesis);
  const synthesisConfidence = asObject(synthesis.confidence);
  const synthesisEvidence = asObject(synthesis.evidence);
  const repair = asObject(job.repair);
  const repairConfidence = asObject(repair.confidence);
  const paths = safePaths(job.relevant_paths);
  return {
    jobId,
    status: rawStatus,
    objective: readString(job.objective, "Project work"),
    deliverables: strings(job.deliverables),
    constraints: strings(job.constraints),
    acceptanceCriteria: strings(job.acceptance_criteria),
    relevantPaths: paths,
    missingInformation: strings(job.missing_information),
    risks: strings(job.risks),
    clarification: clarification(job.clarification),
    plan: {
      findings: objects(plan.current_state_findings).map((item) => ({
        claim: readString(item.claim),
        relativePath: safePath(readString(item.relative_path)),
      })).filter((item) => item.claim && item.relativePath),
      files: safePaths(plan.files_likely_involved),
      steps: strings(plan.steps),
      safetyImpact: readString(plan.safety_impact),
      assumptions: strings(plan.unresolved_assumptions),
    },
    analysis: {
      status: readString(analysis.status, "not_started"),
      findings: objects(analysis.structural_findings).map((item) => ({
        relativePath: safePath(readString(item.relative_path)),
        summary: readString(item.summary),
        parseStatus: readString(item.parse_status, "unknown"),
      })).filter((item) => item.relativePath && item.summary),
      symbols: objects(analysis.relevant_symbols).map((item) => {
        const range = asObject(item.range);
        return {
          relativePath: safePath(readString(item.relative_path)),
          name: readString(item.name),
          kind: readString(item.kind, "symbol"),
          startLine: positiveNumber(range.start_line),
          endLine: positiveNumber(range.end_line),
        };
      }).filter((item) => item.relativePath && item.name),
      coherentFiles: objects(analysis.coherent_file_set).map((item) => ({
        relativePath: safePath(readString(item.relative_path)),
        classification: readString(item.classification, "likely_required"),
        reason: readString(item.reason),
      })).filter((item) => item.relativePath),
      impactedTests: safePaths(analysis.impacted_tests),
      confidence: confidenceValue(confidence.level),
      warnings: strings(confidence.warnings ?? analysis.uncertainties),
      planOnly: analysis.plan_only === true,
      planOnlyReasons: strings(analysis.plan_only_reasons),
      prevalidation: {
        status: readString(prevalidation.status, "not_started"),
        checks: strings(prevalidation.checks),
        warnings: strings(prevalidation.warnings),
      },
    },
    synthesis: {
      status: readString(synthesis.status, "not_started"),
      strategy: readString(synthesis.strategy) || undefined,
      provider: readString(synthesis.provider) || undefined,
      model: readString(synthesis.model) || undefined,
      contractVersion: readString(synthesis.contract_version) || undefined,
      evidence: {
        fileCount: readNumber(synthesisEvidence.file_count),
        excerptCount: readNumber(synthesisEvidence.excerpt_count),
        excerptChars: readNumber(synthesisEvidence.excerpt_chars),
      },
      confidence: confidenceValue(synthesisConfidence.level),
      confidenceReasons: strings(synthesisConfidence.reasons),
      modelClaim: readString(synthesisConfidence.model_claim) || undefined,
      warnings: strings(synthesis.warnings),
      assumptions: strings(synthesis.assumptions),
      requiresClarification: synthesis.requires_clarification === true,
      summary: readString(synthesis.summary) || undefined,
    },
    repair: {
      status: readString(repair.status, "not_started"),
      chainId: readString(repair.repair_chain_id) || undefined,
      cycleId: readString(repair.repair_cycle_id) || undefined,
      cycleNumber: readNumber(repair.cycle_number),
      maxCycles: readNumber(job.max_repair_cycles, 3),
      failureEvidenceId: readString(repair.failure_evidence_id) || undefined,
      diagnosisId: readString(repair.diagnosis_id) || undefined,
      parentPatchId: readString(repair.parent_patch_id) || undefined,
      repairPatchId: readString(repair.repair_patch_id) || undefined,
      commandExecutionId: readString(repair.command_execution_id) || undefined,
      strategy: readString(repair.diagnosis_strategy) || undefined,
      provider: readString(repair.provider) || undefined,
      model: readString(repair.model) || undefined,
      confidence: confidenceValue(repairConfidence.level),
      confidenceReasons: strings(repairConfidence.reasons),
      rootCauses: objects(repair.root_causes).map((item) => ({
        reasonCode: readString(item.reason_code),
        explanation: readString(item.explanation),
        affectedFiles: safePaths(item.affected_files),
        affectedSymbols: strings(item.affected_symbols),
      })).filter((item) => item.reasonCode || item.explanation),
      affectedFiles: safePaths(repair.affected_files),
      affectedSymbols: strings(repair.affected_symbols),
      assumptions: strings(repair.assumptions),
      warnings: strings(repair.warnings),
      clarification: clarification(repair.clarification),
      failedCommandSummary: readString(repair.failed_command_summary) || undefined,
      outputTruncated: repair.failure_output_truncated === true,
      redactionCount: readNumber(repair.failure_redaction_count),
      validationRerunStatus: readString(repair.validation_rerun_status, "not_planned"),
      rollbackAvailable: repair.rollback_available === true,
    },
    patchIds: strings(job.patch_ids),
    commandPlanIds: strings(job.command_plan_ids),
    validationPlan: objects(job.validation_plan),
    validationResults: objects(job.validation_results),
    completionSummary: Object.keys(asObject(job.completion_summary)).length ? asObject(job.completion_summary) : undefined,
    revisionCount: readNumber(job.revision_count),
    maxRevisionCycles: readNumber(job.max_revision_cycles, 3),
    technical: job,
  };
}

export function mergeProjectJobAction(
  current: ProjectJobAction | undefined,
  incoming: ProjectJobAction | null,
): ProjectJobAction | undefined {
  if (!incoming) return current;
  if (!current || current.jobId === incoming.jobId) return incoming;
  return current;
}

function clarification(value: unknown) {
  const item = asObject(value);
  const question = readString(item.question);
  const answer = readString(item.answer);
  return question || answer ? { question: question || undefined, answer: answer || undefined } : undefined;
}

function safePaths(value: unknown): string[] {
  return strings(value).map(safePath).filter(Boolean).slice(0, 20);
}

function safePath(value: string): string {
  const normalized = value.replace(/\\/g, "/");
  if (!normalized || normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized) || normalized.includes("../")) return "";
  return normalized;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 30) : [];
}

function objects(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(asObject).filter((item) => Object.keys(item).length).slice(0, 30) : [];
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readString(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function readNumber(value: unknown, fallback = 0): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function positiveNumber(value: unknown): number | undefined { return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : undefined; }
function confidenceValue(value: unknown): "high" | "medium" | "low" | "unknown" { return value === "high" || value === "medium" || value === "low" ? value : "unknown"; }
