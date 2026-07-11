import type { AssignmentRequirementStatus } from "../clients/astraClient";

export type EvidenceFilter = "all" | "missing" | "failed" | "manual_review" | "partially_verified" | "verified";

export function requirementMatchesFilter(status: AssignmentRequirementStatus, filter: EvidenceFilter): boolean {
  if (filter === "all") return true;
  if (filter === "manual_review") return status === "requires_manual_review";
  return status === filter;
}

export function requirementStatusLabel(status: AssignmentRequirementStatus): string {
  return status.replace(/_/g, " ");
}
