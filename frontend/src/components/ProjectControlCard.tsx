import { Activity, CheckCircle2, ChevronDown, CircleAlert, FileText, ShieldCheck, X } from "lucide-react";
import type { CanonicalProjectActionDescriptor } from "../types/contracts";
import type { CanonicalProjectAction } from "../state/projectControlState";

export function ProjectControlCard({
  project,
  busy = false,
  onAction,
}: {
  project: CanonicalProjectAction;
  busy?: boolean;
  onAction: (action: CanonicalProjectActionDescriptor) => void;
}) {
  const completed = project.progress.completed_work_units ?? 0;
  const total = project.progress.total_work_units ?? 0;
  const passed = project.verificationSummary.passed ?? 0;
  const criteria = project.verificationSummary.total ?? Object.keys(project.criterionStates).length;
  const executionStatus = project.execution.cancellationStatus
    ?? project.execution.workerStatus
    ?? project.execution.dispatchStatus
    ?? project.execution.attemptStatus;
  return <div className="action-card project-delivery-card" data-project-run-id={project.projectRunId}>
    <div className="card-heading"><div><span className="eyebrow">Canonical project</span><h2>Bounded project task</h2></div><span className={`status status-${project.lifecycleState}`}>{project.lifecycleState.replace(/_/g, " ")}</span></div>
    <div className="delivery-progress" aria-label="Canonical project progress">
      <span><strong>{completed} of {total}</strong> work units complete</span>
      <span><strong>{passed} of {criteria}</strong> verification criteria passed</span>
      <span><strong>State {project.stateVersion}</strong> canonical revision</span>
    </div>
    <section className="job-section"><h3>Control state</h3><div className="synthesis-facts">
      <span><strong>Approval</strong>{project.approvalState.replace(/_/g, " ")}</span>
      <span><strong>Manifest</strong>{project.manifestComplete ? "complete" : "incomplete"}</span>
      <span><strong>Projection</strong>{project.projection.status ?? "current"}{project.projection.lag ? ` · ${project.projection.lag} events behind` : ""}</span>
      <span><strong>Artifacts</strong>{project.artifacts.length}</span>
    </div></section>
    {project.coordinator && <div className="progress-line"><Activity className={project.coordinator.status === "claimed" ? "spin" : ""} size={16} /><span>Coordinator: {project.coordinator.intent_type.replace(/_/g, " ")} · {project.coordinator.status}</span></div>}
    {(project.execution.attemptId || project.execution.dispatchId || project.execution.workerRequestId || project.execution.cancellationId) && <section className="job-section"><h3>Isolated execution</h3><div className="synthesis-facts">
      <span><strong>Attempt</strong>{project.execution.attemptType?.replace(/_/g, " ") ?? "pending"}</span>
      <span><strong>Status</strong>{executionStatus?.replace(/_/g, " ") ?? "pending"}</span>
      <span><strong>Identity</strong>{project.execution.workerRequestId ?? project.execution.dispatchId ?? project.execution.attemptId ?? "persisting"}</span>
      <span><strong>Cancellation</strong>{project.execution.cancellationStatus?.replace(/_/g, " ") ?? "not requested"}</span>
    </div></section>}
    {Object.keys(project.repairState).length > 0 && <div className="result failed"><CircleAlert size={17} /><div><strong>Bounded repair state</strong><p>{String(project.repairState.status ?? "prepared").replace(/_/g, " ")}. A repair is never applied automatically.</p></div></div>}
    {project.blockedReason && <div className="result failed"><CircleAlert size={17} /><div><strong>Project paused safely</strong><p>{project.blockedReason}</p></div></div>}
    {project.execution.failureClassification && <div className="result failed"><CircleAlert size={17} /><div><strong>Execution failure</strong><p>{project.execution.failureClassification.replace(/_/g, " ")}</p></div></div>}
    {project.projection.failureClassification && <div className="result failed"><CircleAlert size={17} /><div><strong>Projection recovery required</strong><p>{project.projection.failureClassification.replace(/_/g, " ")}</p></div></div>}
    {project.terminal && project.lifecycleState === "completed" && <div className="result completed"><CheckCircle2 size={17} /><div><strong>Canonical project completed</strong><p>The durable control plane recorded the terminal state.</p></div></div>}
    <div className="button-row">
      {project.nextPermittedActions.map((action) => <button
        key={`${action.action}:${action.expected_state_version}:${action.artifact_id ?? "none"}`}
        className={action.action === "cancel_project" ? "secondary-button danger" : "primary-button"}
        disabled={busy}
        onClick={() => onAction(action)}
      >{action.action === "cancel_project" ? <X size={16} /> : action.action.includes("approve") ? <ShieldCheck size={16} /> : <FileText size={16} />}{action.label}</button>)}
    </div>
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body">
      <span>Project: {project.projectRunId}</span><span>Pending: {project.pendingUserAction ?? "none"}</span>
      <pre>{JSON.stringify(project.response, null, 2)}</pre>
    </div></details>
  </div>;
}
