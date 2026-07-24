import type { CanonicalProjectResponse } from "../types/contracts";

export interface ProjectOption {
  projectRunId: string;
  label: string;
}

/** Priority per Phase 9B spec: canonical title (none exists on the read
 * model today), then a repository/scope label already on the read model,
 * then a shortened project_run_id. Never fabricates a name. */
function projectLabel(projectRunId: string, fingerprint: string, workspaceId: string): string {
  if (fingerprint) return fingerprint;
  if (workspaceId) return workspaceId;
  return projectRunId.length > 8 ? `${projectRunId.slice(0, 8)}…` : projectRunId;
}

/** Defensive: a malformed or partially-shaped project response is dropped
 * rather than crashing the selector, since this only feeds a UI list. */
export function deriveProjectOptions(
  projects: CanonicalProjectResponse[] | null | undefined,
): ProjectOption[] {
  if (!Array.isArray(projects)) return [];
  const options: ProjectOption[] = [];
  for (const item of projects) {
    const projectRunId = item?.project?.project_run_id;
    if (typeof projectRunId !== "string" || !projectRunId) continue;
    const fingerprint = typeof item.project?.repository_root_fingerprint === "string"
      ? item.project.repository_root_fingerprint
      : "";
    const workspaceId = typeof item.project?.workspace_id === "string" ? item.project.workspace_id : "";
    options.push({ projectRunId, label: projectLabel(projectRunId, fingerprint, workspaceId) });
  }
  return options.sort((a, b) => a.label.localeCompare(b.label));
}

export interface ActiveProjectSelection {
  /** The project ID to actually attach to future chat requests -- null
   * whenever there is no selection, or the stored selection is stale. */
  projectRunId: string | null;
  /** True when a stored selection exists but no longer appears among the
   * conversation's live projects (deleted/unavailable) -- the selection is
   * not attached to new requests until the user resolves it. */
  stale: boolean;
}

/** Cross-checks a hydrated active_project_run_id against the conversation's
 * live project list. Backend hydration is authoritative: this never trusts
 * a value that isn't corroborated by the conversation's own projects. */
export function resolveActiveProjectSelection(
  activeProjectRunId: string | null | undefined,
  projects: CanonicalProjectResponse[] | null | undefined,
): ActiveProjectSelection {
  if (!activeProjectRunId) return { projectRunId: null, stale: false };
  const options = deriveProjectOptions(projects);
  const known = options.some((option) => option.projectRunId === activeProjectRunId);
  return known ? { projectRunId: activeProjectRunId, stale: false } : { projectRunId: null, stale: true };
}

/** The single shared fragment both /chat/run and /chat/stream request
 * bodies merge in -- one construction point so run/stream can never drift
 * on which project is attached to a given send. */
export function chatProjectRequestField(selection: ActiveProjectSelection): { project_run_id: string | null } {
  return { project_run_id: selection.stale ? null : selection.projectRunId };
}
