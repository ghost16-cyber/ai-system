import type { GroundedReportSection, ReportExportReadiness, ReportSectionState } from "../clients/astraClient";

export type ReportSectionFilter = "all" | "supported" | "placeholders" | "stale" | "failed";

export function reportStateLabel(state: ReportSectionState): string {
  return state.replace(/_/g, " ");
}

export function reportSectionMatches(section: GroundedReportSection, filter: ReportSectionFilter): boolean {
  if (filter === "all") return true;
  if (filter === "supported") return ["verified", "manually_accepted", "partially_supported"].includes(section.verification_state);
  if (filter === "placeholders") return section.placeholders.length > 0;
  if (filter === "stale") return section.verification_state === "stale";
  return section.verification_state === "unsupported" || section.warnings.some((warning) => /failed|conflict/i.test(warning));
}

export function toggleSubmissionFile(selected: string[], path: string): string[] {
  return selected.includes(path) ? selected.filter((item) => item !== path) : [...selected, path];
}

export function hasVisiblePlaceholders(section: GroundedReportSection): boolean {
  return section.placeholders.length > 0 || section.grounded_content_blocks.some((block) => block.block_type === "placeholder");
}

export function exportReadinessIsBlocked(readiness: ReportExportReadiness): boolean {
  return readiness.status === "blocked" || readiness.export_blockers.length > 0;
}
