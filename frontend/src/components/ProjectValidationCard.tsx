import { useRef, useState } from "react";
import type { ProjectValidationAction } from "../state/projectValidationState";

export type ValidationOperation = "prepare" | "start" | "evaluate" | "pause" | "resume" | "recover" | "restore" | "cancel";
export type ValidationReviewAction =
  | "approve_as_delivery_ready" | "approve_with_notes" | "request_remediation"
  | "reject_delivery" | "request_scope_change" | "cancel_engagement";

export function ProjectValidationCard({
  action, onOperation, onReview,
}: {
  action: ProjectValidationAction;
  onOperation: (operation: ValidationOperation) => Promise<void>;
  onReview: (reviewAction: ValidationReviewAction, notes: string) => Promise<void>;
}) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const runOnce = async (operation: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try { await operation(); } finally { lock.current = false; setBusy(false); }
  };
  const run = action.run;
  const reviewReady = action.status === "awaiting_human_review" && Boolean(run?.resultHash);
  return <div className="action-card project-validation-card">
    <div className="card-heading"><div><span className="eyebrow">Project validation</span><h2>{heading(action.status)}</h2></div><span className={`status status-${action.status}`}>{action.status.replace(/_/g, " ")}</span></div>
    <section className="job-section"><h3>Approved objective</h3><p>{action.objective}</p></section>
    {action.workspace && <section className="job-section validation-summary-grid"><span><strong>{action.workspace.name}</strong> workspace</span><span><strong>{action.workspace.isolated ? "Isolated" : "Authorized"}</strong> validation mode</span>{action.baseline && <><span><strong>{action.baseline.fileCount}</strong> baseline files</span><span><strong>{formatBytes(action.baseline.totalBytes)}</strong> baseline size</span></>}</section>}
    {run && <>
      <section className="job-section"><h3>Acceptance criteria</h3>{run.criteria.length ? <div className="validation-list">{run.criteria.map((criterion) => <div key={criterion.id} className={`validation-row result-${criterion.result}`}><div><strong>{criterion.text}</strong><small>{criterion.result.replace(/_/g, " ")}{criterion.humanReviewRequired ? " · human review" : ""}</small>{criterion.explanation && <p>{criterion.explanation}</p>}</div></div>)}</div> : <p className="muted">Acceptance checks have not been evaluated yet.</p>}</section>
      {run.artifacts.length > 0 && <section className="job-section"><h3>Deliverables</h3><div className="validation-list">{run.artifacts.map((artifact) => <div key={artifact.id} className={`validation-row ${artifact.exists ? "result-passed" : "result-failed"}`}><div><strong>{artifact.name}</strong><small>{artifact.type.replace(/_/g, " ")} · {artifact.exists ? formatBytes(artifact.sizeBytes) : "missing"}</small>{artifact.warning && <p>{artifact.warning}</p>}</div></div>)}</div>{run.missingDeliverables.length > 0 && <p className="validation-blocker">Missing: {run.missingDeliverables.join(", ")}</p>}</section>}
      {run.regression && <section className="job-section"><h3>Regression safety</h3><p className={run.regression.blocking ? "validation-blocker" : "validation-ok"}>{run.regression.summary}</p>{run.regression.regressedTests.length > 0 && <p>Regressed tests: {run.regression.regressedTests.join(", ")}</p>}</section>}
      {run.quality && <section className="job-section"><h3>Quality review</h3><div className="validation-score"><strong>{Math.round(run.quality.score)}/100</strong><span>minimum {Math.round(run.quality.minimum)}</span></div><div className="validation-dimensions">{run.quality.dimensions.map((dimension) => <div key={dimension.name}><span>{dimension.name}</span><strong>{Math.round(dimension.score)}</strong><small>{dimension.explanation}</small></div>)}</div>{run.quality.blockers.length > 0 && <ul className="validation-blocker">{run.quality.blockers.map((item) => <li key={item}>{item}</li>)}</ul>}</section>}
      {run.findings.length > 0 && <section className="job-section"><h3>Required remediation</h3><ul>{run.findings.map((finding) => <li key={finding.id}><strong>{finding.severity}: </strong>{finding.summary}<small>Route: {finding.route.replace(/_/g, " ")}</small></li>)}</ul></section>}
      <section className="job-section"><h3>Resource usage</h3><div className="validation-budget">{Object.entries(run.budgetUsage).filter(([, value]) => value > 0).slice(0, 8).map(([key, value]) => <span key={key}><strong>{value}</strong> {key.replace(/_/g, " ")}</span>)}</div></section>
    </>}
    <div className="button-row">
      {action.status === "created" && <button className="primary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("prepare"))}>Prepare validation</button>}
      {["ready", "remediation_required"].includes(action.status) && <button className="primary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("start"))}>Start validation run</button>}
      {action.status === "running" && <><button className="primary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("evaluate"))}>Evaluate completed work</button><button className="secondary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("pause"))}>Pause safely</button></>}
      {action.status === "execution_paused" && <><button className="primary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("resume"))}>Resume validation</button><button className="secondary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("restore"))}>Restore baseline</button></>}
      {["failed", "recovering"].includes(action.status) && <button className="secondary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("recover"))}>Recover safely</button>}
      {["remediation_required", "delivery_rejected"].includes(action.status) && action.baseline && <button className="secondary-button" disabled={busy} onClick={() => void runOnce(() => onOperation("restore"))}>Restore baseline</button>}
      {!['delivery_ready', 'cancelled'].includes(action.status) && <button className="secondary-button danger" disabled={busy} onClick={() => void runOnce(() => onOperation("cancel"))}>Cancel validation</button>}
    </div>
    {reviewReady && <section className="job-section validation-human-review"><h3>Human delivery review</h3><p>Review the exact validated result. Approval does not send files, deploy, or approve new commands.</p><textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} disabled={busy} placeholder="Optional review notes" /><div className="button-row"><button className="primary-button" disabled={busy} onClick={() => void runOnce(() => onReview("approve_as_delivery_ready", notes))}>Approve delivery-ready</button><button className="secondary-button" disabled={busy} onClick={() => void runOnce(() => onReview("approve_with_notes", notes))}>Approve with notes</button><button className="secondary-button danger" disabled={busy} onClick={() => void runOnce(() => onReview("request_remediation", notes))}>Request remediation</button><button className="secondary-button danger" disabled={busy} onClick={() => void runOnce(() => onReview("reject_delivery", notes))}>Reject</button></div></section>}
    {action.status === "delivery_ready" && <div className="result completed"><strong>Human-approved as delivery-ready.</strong> Nothing was sent or deployed automatically.</div>}
    {action.status === "budget_exceeded" && <div className="result failed">Validation paused because a configured safety limit was reached.</div>}
    <details className="technical"><summary>Technical details</summary><pre>{JSON.stringify({ campaignId: action.campaignId, scopeRevisionId: action.scopeRevisionId, scopeHash: action.scopeHash, resultHash: run?.resultHash, stateVersion: action.stateVersion, runVersion: run?.stateVersion }, null, 2)}</pre></details>
  </div>;
}

function heading(status: ProjectValidationAction["status"]): string {
  if (status === "awaiting_human_review") return "Review delivery readiness";
  if (status === "delivery_ready") return "Project is delivery-ready";
  if (status === "remediation_required") return "Project needs remediation";
  if (status === "budget_exceeded") return "Validation paused at a safety limit";
  return "Validate the approved project";
}
function formatBytes(value: number): string { return value < 1024 ? `${value} B` : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }
