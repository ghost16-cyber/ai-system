import { CheckCircle2, ClipboardCheck, RefreshCw, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import {
  HttpAstraClient,
  type AssignmentRequirementVerification,
  type AssignmentVerificationSnapshot,
} from "../clients/astraClient";
import {
  requirementMatchesFilter,
  requirementStatusLabel,
  type EvidenceFilter,
} from "../state/assignmentReadinessState";

interface Props {
  client: HttpAstraClient;
  assignmentId: string | null;
  workspacePath: string;
  assignmentOutput: Record<string, unknown>;
}

const filters: Array<{ id: EvidenceFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "missing", label: "Missing" },
  { id: "failed", label: "Failed" },
  { id: "manual_review", label: "Manual review" },
  { id: "partially_verified", label: "Partially verified" },
  { id: "verified", label: "Verified" },
];

export function AssignmentEvidenceReadinessSection({
  client,
  assignmentId,
  workspacePath,
  assignmentOutput,
}: Props) {
  const [snapshot, setSnapshot] = useState<AssignmentVerificationSnapshot | null>(null);
  const [filter, setFilter] = useState<EvidenceFilter>("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewDecision, setReviewDecision] = useState<Record<string, string>>({});
  const [reviewEvidence, setReviewEvidence] = useState<Record<string, string>>({});
  const [reviewNote, setReviewNote] = useState<Record<string, string>>({});

  const visible = useMemo(
    () => (snapshot?.requirements ?? []).filter((item) => requirementMatchesFilter(item.status, filter)),
    [filter, snapshot],
  );

  async function verify() {
    if (!assignmentId || !workspacePath.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setSnapshot(await client.verifyAssignmentEvidence(assignmentId, {
        workspace_path: workspacePath,
        assignment_output: assignmentOutput,
      }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Evidence verification failed.");
    } finally {
      setBusy(false);
    }
  }

  async function review(requirement: AssignmentRequirementVerification) {
    if (!assignmentId) return;
    setBusy(true);
    setError(null);
    try {
      await client.reviewAssignmentEvidence(assignmentId, {
        workspace_path: workspacePath,
        requirement_id: requirement.requirement_id,
        evidence_reference: reviewEvidence[requirement.requirement_id],
        decision: reviewDecision[requirement.requirement_id],
        note: reviewNote[requirement.requirement_id] ?? "",
      });
      await verify();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Manual review could not be recorded.");
      setBusy(false);
    }
  }

  return (
    <section className="panel evidence-readiness">
      <div className="panel-title-row">
        <div className="panel-title"><ClipboardCheck size={18} /><h2>Evidence and Readiness</h2></div>
        <button className="secondary-button" onClick={() => void verify()} disabled={busy || !assignmentId || !workspacePath.trim()}>
          {busy ? <RefreshCw size={16} className="spin" /> : <ShieldCheck size={16} />} Verify evidence
        </button>
      </div>
      <div className="notice subtle">
        <ShieldCheck size={16} />
        Verification reads existing files and controlled execution records only. Evidence coverage does not establish academic completion or correctness.
      </div>
      {!assignmentId && <div className="empty-inline">Choose one assignment before verifying evidence.</div>}
      {assignmentId && !workspacePath.trim() && <div className="empty-inline">Enter the confined assignment workspace path first.</div>}
      {error && <div className="notice amber">{error}</div>}

      {snapshot && (
        <>
          <div className="readiness-metrics">
            <div><strong>{snapshot.readiness.evidence_coverage_percentage}%</strong><span>Evidence coverage</span></div>
            <div><strong>{snapshot.readiness.verified_count}</strong><span>Verified</span></div>
            <div><strong>{snapshot.readiness.partially_verified_count}</strong><span>Partial</span></div>
            <div><strong>{snapshot.readiness.missing_count}</strong><span>Missing</span></div>
            <div><strong>{snapshot.readiness.failed_count}</strong><span>Failed</span></div>
            <div><strong>{snapshot.readiness.manual_review_count}</strong><span>Manual review</span></div>
          </div>
          <div className="filter-row" role="group" aria-label="Evidence status filters">
            {filters.map((item) => (
              <button key={item.id} className={filter === item.id ? "filter-button active" : "filter-button"} onClick={() => setFilter(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
          <div className="requirement-list">
            {visible.map((requirement) => {
              const references = [
                ...requirement.linked_workspace_files.map((path) => `file:${path}`),
                ...requirement.linked_execution_evidence,
              ];
              const selectedReference = reviewEvidence[requirement.requirement_id] ?? references[0] ?? "";
              return (
                <article className="requirement-card" key={requirement.requirement_id}>
                  <div className="execution-plan-header">
                    <strong>{requirement.title}</strong>
                    <span className={`status-pill requirement-${requirement.status}`}>{requirementStatusLabel(requirement.status)}</span>
                  </div>
                  <span>{requirement.requirement_category} · Confidence {Math.round(requirement.confidence * 100)}%</span>
                  <p>{requirement.description}</p>
                  {requirement.linked_workspace_files.length > 0 && <div><strong>Workspace evidence</strong><div className="tag-row">{requirement.linked_workspace_files.map((path) => <code key={path}>{path}</code>)}</div></div>}
                  {requirement.linked_execution_evidence.length > 0 && <div><strong>Execution evidence</strong><div className="tag-row">{requirement.linked_execution_evidence.map((reference) => <code key={reference}>{reference}</code>)}</div></div>}
                  {requirement.warnings.map((warning) => <div className="notice amber" key={warning}>{warning}</div>)}
                  {requirement.reviewer_notes.map((note) => <div className="notice subtle" key={note}>Reviewer note: {note}</div>)}
                  {references.length > 0 && (
                    <div className="manual-review-controls">
                      <label>Evidence
                        <select value={selectedReference} onChange={(event) => setReviewEvidence((current) => ({ ...current, [requirement.requirement_id]: event.target.value }))}>
                          {references.map((reference) => <option key={reference} value={reference}>{reference}</option>)}
                        </select>
                      </label>
                      <label>Manual decision
                        <select value={reviewDecision[requirement.requirement_id] ?? ""} onChange={(event) => setReviewDecision((current) => ({ ...current, [requirement.requirement_id]: event.target.value }))}>
                          <option value="">Choose explicitly</option>
                          <option value="accepted">Accepted</option>
                          <option value="rejected">Rejected</option>
                          <option value="needs_replacement">Needs replacement</option>
                        </select>
                      </label>
                      <label>Reviewer note
                        <input value={reviewNote[requirement.requirement_id] ?? ""} onChange={(event) => setReviewNote((current) => ({ ...current, [requirement.requirement_id]: event.target.value }))} />
                      </label>
                      <button className="secondary-button" onClick={() => void review(requirement)} disabled={busy || !reviewDecision[requirement.requirement_id]}>
                        <CheckCircle2 size={15} /> Record review
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
            {visible.length === 0 && <div className="empty-inline">No requirements match this filter.</div>}
          </div>
          <div className="two-column">
            <div><h3 className="section-subtitle">Blocking issues</h3>{snapshot.readiness.blocking_issues.length ? <ul>{snapshot.readiness.blocking_issues.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No deterministic missing or failed blockers detected.</p>}</div>
            <div><h3 className="section-subtitle">Recommended next actions</h3><ul>{snapshot.readiness.recommended_next_actions.map((item) => <li key={item}>{item}</li>)}</ul></div>
          </div>
        </>
      )}
      {!snapshot && assignmentId && workspacePath.trim() && <div className="empty-inline">Run deterministic verification to inventory evidence and calculate readiness.</div>}
    </section>
  );
}
