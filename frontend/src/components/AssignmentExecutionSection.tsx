import { Activity, Play, ShieldCheck, Wrench } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  HttpAstraClient,
  type AssignmentCommandRecord,
  type AssignmentExecutionSuggestion,
  type AssignmentExecutionSummary,
} from "../clients/astraClient";
import { mapAssignmentExecutionState } from "../state/assignmentExecutionState";

interface Props {
  client: HttpAstraClient;
  assignmentId: string | null;
  workspacePath: string;
}

export function AssignmentExecutionSection({ client, assignmentId, workspacePath }: Props) {
  const [suggestions, setSuggestions] = useState<AssignmentExecutionSuggestion[]>([]);
  const [summary, setSummary] = useState<AssignmentExecutionSummary | null>(null);
  const [approvalText, setApprovalText] = useState<Record<string, string>>({});
  const [approvalTokens, setApprovalTokens] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!assignmentId || !workspacePath.trim()) {
      setSuggestions([]);
      setSummary(null);
      return;
    }
    setError(null);
    try {
      const [suggested, execution] = await Promise.all([
        client.getAssignmentExecutionSuggestions(assignmentId, workspacePath),
        client.getAssignmentExecutionSummary(assignmentId, workspacePath),
      ]);
      setSuggestions(suggested.suggestions);
      setSummary(execution);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load controlled execution state.");
    }
  }, [assignmentId, client, workspacePath]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function plan(suggestion: AssignmentExecutionSuggestion) {
    if (!assignmentId) return;
    setBusy(`plan:${suggestion.action}:${suggestion.target ?? ""}`);
    setError(null);
    try {
      await client.planAssignmentCommand({
        assignment_id: assignmentId,
        workspace_path: workspacePath,
        assignment_task: suggestion.purpose,
        expected_result: suggestion.expected_result,
        action: suggestion.action,
        target: suggestion.target,
        timeout_seconds: suggestion.timeout_seconds,
      });
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Command planning failed.");
    } finally {
      setBusy(null);
    }
  }

  async function approve(command: AssignmentCommandRecord) {
    if (!assignmentId) return;
    setBusy(`approve:${command.plan_id}`);
    setError(null);
    try {
      const result = await client.approveAssignmentCommand(command.plan_id, {
        assignment_id: assignmentId,
        workspace_path: workspacePath,
        confirmation: approvalText[command.plan_id] ?? "",
      });
      setApprovalTokens((current) => ({ ...current, [command.plan_id]: result.approval_token }));
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Approval failed.");
    } finally {
      setBusy(null);
    }
  }

  async function execute(command: AssignmentCommandRecord) {
    if (!assignmentId) return;
    setBusy(`execute:${command.plan_id}`);
    setError(null);
    try {
      await client.executeAssignmentCommand(command.plan_id, {
        assignment_id: assignmentId,
        workspace_path: workspacePath,
        approval_token: approvalTokens[command.plan_id] ?? "",
      });
      setApprovalTokens((current) => {
        const next = { ...current };
        delete next[command.plan_id];
        return next;
      });
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Execution failed.");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel assignment-execution">
      <div className="panel-title-row">
        <div className="panel-title"><ShieldCheck size={18} /><h2>Controlled Execution</h2></div>
        <button className="secondary-button" onClick={() => void refresh()} disabled={!assignmentId || !workspacePath.trim()}>
          <Activity size={16} /> Refresh state
        </button>
      </div>
      <div className="notice subtle">
        <ShieldCheck size={16} />
        This is allowlisted, approval-gated controlled execution—not an operating-system sandbox. It does not provide filesystem or network isolation.
      </div>
      {!assignmentId && <div className="empty-inline">Choose one assignment to review validation actions.</div>}
      {assignmentId && !workspacePath.trim() && <div className="empty-inline">Enter the assignment workspace path first.</div>}
      {error && <div className="notice amber">{error}</div>}

      {suggestions.length > 0 && (
        <div className="execution-grid">
          {suggestions.map((suggestion) => {
            const key = `${suggestion.action}:${suggestion.target ?? ""}`;
            return (
              <article className="execution-card" key={key}>
                <strong>{suggestion.purpose}</strong>
                <code>Executable: {suggestion.executable}</code>
                <code>Arguments: {JSON.stringify(suggestion.arguments)}</code>
                <span>Working directory: assignment workspace ({suggestion.working_directory})</span>
                <span>Timeout: {suggestion.timeout_seconds}s · Expected: {suggestion.expected_result}</span>
                <button className="secondary-button" onClick={() => void plan(suggestion)} disabled={busy !== null}>
                  <Wrench size={15} /> Plan
                </button>
              </article>
            );
          })}
        </div>
      )}

      {summary?.planned_commands.map((command) => {
        const state = mapAssignmentExecutionState(command.status);
        const phrase = `APPROVE ${command.plan_id}`;
        return (
          <article className="execution-plan" key={command.plan_id}>
            <div className="execution-plan-header">
              <strong>{command.command}</strong>
              <span className={`status-pill state-${state}`}>{state}</span>
            </div>
            <code>Executable: {command.executable}</code>
            <code>Arguments: {JSON.stringify(command.arguments)}</code>
            <span>Working directory: assignment workspace ({command.working_directory}) · Timeout: {command.timeout_seconds}s</span>
            <span>Task: {command.assignment_task} · Expected: {command.expected_result}</span>
            {command.approved_artifacts.length > 0 && (
              <div className="hash-list">
                {command.approved_artifacts.map((artifact) => <code key={artifact.path}>{artifact.path} · SHA-256 {artifact.sha256}</code>)}
              </div>
            )}
            {state === "pending" && (
              <div className="approval-row">
                <label>Type exactly <code>{phrase}</code>
                  <input value={approvalText[command.plan_id] ?? ""} onChange={(event) => setApprovalText((current) => ({ ...current, [command.plan_id]: event.target.value }))} />
                </label>
                <button className="secondary-button" onClick={() => void approve(command)} disabled={busy !== null || approvalText[command.plan_id] !== phrase}>
                  <ShieldCheck size={15} /> Approve
                </button>
              </div>
            )}
            {state === "approved" && (
              <button className="primary-button" onClick={() => void execute(command)} disabled={busy !== null || !approvalTokens[command.plan_id]}>
                <Play size={15} /> Execute
              </button>
            )}
            {(command.stdout || command.stderr) && (
              <div className="execution-logs">
                <label>Redacted stdout<pre>{command.stdout || "(empty)"}</pre></label>
                <label>Redacted stderr<pre>{command.stderr || "(empty)"}</pre></label>
              </div>
            )}
            {state === "completed" && <div className="notice subtle">Exit code {command.exit_code}. This is evidence only; academic task completion was not inferred.</div>}
            {state === "failed" && <div className="notice amber">Exit code {command.exit_code ?? "unavailable"}. Failed validation remains recorded as evidence.</div>}
            {state === "expired" && <div className="notice amber">Approval expired. Create a new plan to continue.</div>}
          </article>
        );
      })}
      {summary && summary.planned_commands.length === 0 && suggestions.length === 0 && (
        <div className="empty-inline">No applicable allowlisted validation actions were detected.</div>
      )}
    </section>
  );
}
