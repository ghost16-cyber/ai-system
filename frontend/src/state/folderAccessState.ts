import type { ChatActionStatus } from "./chatActionState";

export interface FolderInventoryItem {
  relativePath: string;
  filename: string;
  classification: string;
  extension: string;
  sizeBytes: number;
  modifiedAt?: string | null;
  fingerprint: string;
  status: "readable" | "ignored";
  ignoreReason?: string | null;
}

export interface FolderInventorySummary {
  totalDiscovered: number;
  readable: number;
  ignored: number;
  assignments: number;
  datasets: number;
  sourceFiles: number;
  reports: number;
  evidenceFiles: number;
  configurationFiles: number;
  otherFiles: number;
  warningCount: number;
}

export interface FolderDiff {
  added: number;
  changed: number;
  deleted: number;
  unchanged: number;
}

export interface FolderScanDiagnostics {
  totalIndexed: number;
  totalEligible: number;
  eligibleOmitted: number;
  exemptDatasetFiles: number;
  ignoredGenerated: number;
  ignoredSensitive: number;
  ignoredUnsupported: number;
  ignoredTemporary: number;
  oversized: number;
  unreadable: number;
  fileCountBudgetExceeded: boolean;
  totalSizeBudgetExceeded: boolean;
  maxDepthReached: boolean;
  diagnosticCapReached: boolean;
}

export interface FolderScanLimits {
  maxFiles: number;
  maxFileSizeBytes: number;
  maxTotalSizeBytes: number;
  maxDepth: number;
}

export interface FolderAccessAction {
  actionId?: string;
  status: ChatActionStatus | "scanning";
  displayPath: string;
  requestedDisplayPath: string;
  resultSummary?: string;
  error?: string;
  lastScannedAt?: string | null;
  scanCount: number;
  summary: FolderInventorySummary;
  diff: FolderDiff;
  warnings: string[];
  inventory: FolderInventoryItem[];
  complete: boolean;
  diagnostics: FolderScanDiagnostics;
  limits: FolderScanLimits;
}

export function folderAccessActionFromPayload(
  payload: Record<string, unknown> | null | undefined,
): FolderAccessAction | null {
  if (!payload || payload.action_type !== "folder_access") return null;
  const details = asRecord(payload.technical_details);
  const folder = asRecord(details?.folder_action);
  if (!folder) return null;
  const summary = asRecord(folder.summary);
  const diff = asRecord(folder.diff);
  const diagnostics = asRecord(folder.diagnostics);
  const limits = asRecord(folder.limits);
  const inventory = Array.isArray(folder.inventory)
    ? folder.inventory.flatMap((item) => {
      const entry = asRecord(item);
      if (!entry) return [];
      const relativePath = readString(entry.relative_path);
      if (!relativePath || looksAbsolute(relativePath)) return [];
      return [{
        relativePath,
        filename: readString(entry.filename),
        classification: readString(entry.classification, "other"),
        extension: readString(entry.extension),
        sizeBytes: readNumber(entry.size_bytes),
        modifiedAt: readString(entry.modified_at) || null,
        fingerprint: readString(entry.fingerprint),
        status: readString(entry.status) === "ignored" ? "ignored" as const : "readable" as const,
        ignoreReason: readString(entry.ignore_reason) || null,
      }];
    })
    : [];
  return {
    actionId: readString(folder.action_id) || readString(payload.action_id) || undefined,
    status: readStatus(folder.status) || readStatus(payload.status) || "awaiting_approval",
    displayPath: readString(folder.approved_root_display)
      || readString(folder.display_path)
      || readString(folder.requested_path, "Selected folder"),
    requestedDisplayPath: readString(folder.display_path)
      || readString(folder.requested_path, "Selected folder"),
    resultSummary: readString(folder.result_summary) || readString(payload.result_summary) || undefined,
    error: readString(folder.error) || readString(payload.error) || undefined,
    lastScannedAt: readString(folder.last_scanned_at) || null,
    scanCount: readNumber(folder.scan_count),
    summary: {
      totalDiscovered: readNumber(summary?.total_discovered),
      readable: readNumber(summary?.readable),
      ignored: readNumber(summary?.ignored),
      assignments: readNumber(summary?.assignments),
      datasets: readNumber(summary?.datasets),
      sourceFiles: readNumber(summary?.source_files),
      reports: readNumber(summary?.reports),
      evidenceFiles: readNumber(summary?.evidence_files),
      configurationFiles: readNumber(summary?.configuration_files),
      otherFiles: readNumber(summary?.other_files),
      warningCount: readNumber(summary?.warning_count),
    },
    diff: {
      added: readNumber(diff?.added),
      changed: readNumber(diff?.changed),
      deleted: readNumber(diff?.deleted),
      unchanged: readNumber(diff?.unchanged),
    },
    warnings: readStringArray(folder.warnings),
    inventory,
    complete: folder.complete !== false,
    diagnostics: {
      totalIndexed: readNumber(diagnostics?.total_indexed),
      totalEligible: readNumber(diagnostics?.total_eligible),
      eligibleOmitted: readNumber(diagnostics?.eligible_omitted),
      exemptDatasetFiles: readNumber(diagnostics?.exempt_dataset_files),
      ignoredGenerated: readNumber(diagnostics?.ignored_generated),
      ignoredSensitive: readNumber(diagnostics?.ignored_sensitive),
      ignoredUnsupported: readNumber(diagnostics?.ignored_unsupported),
      ignoredTemporary: readNumber(diagnostics?.ignored_temporary),
      oversized: readNumber(diagnostics?.oversized),
      unreadable: readNumber(diagnostics?.unreadable),
      fileCountBudgetExceeded: diagnostics?.file_count_budget_exceeded === true,
      totalSizeBudgetExceeded: diagnostics?.total_size_budget_exceeded === true,
      maxDepthReached: diagnostics?.max_depth_reached === true,
      diagnosticCapReached: diagnostics?.diagnostic_cap_reached === true,
    },
    limits: {
      maxFiles: readNumber(limits?.max_files),
      maxFileSizeBytes: readNumber(limits?.max_file_size_bytes),
      maxTotalSizeBytes: readNumber(limits?.max_total_size_bytes),
      maxDepth: readNumber(limits?.max_depth),
    },
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.trim() : fallback;
}

function readNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function readStatus(value: unknown): FolderAccessAction["status"] | null {
  return typeof value === "string" && [
    "awaiting_approval",
    "approving",
    "approved",
    "running",
    "scanning",
    "completed",
    "failed",
    "cancelled",
  ].includes(value)
    ? value as FolderAccessAction["status"]
    : null;
}

function looksAbsolute(path: string): boolean {
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path);
}
