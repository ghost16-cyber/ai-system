import type { AssignmentCommandRecord } from "../clients/astraClient";
import type { ChatActionStatus } from "./chatActionState";

export type ChatCommandStatus = ChatActionStatus;

export interface CommandResultPresentation {
  summary: string;
  errorTail: string;
}

interface ApprovalResult {
  plan: AssignmentCommandRecord;
  approval_token: string;
}

interface CommandLifecycleCalls {
  approve: (planId: string, request: Record<string, unknown>) => Promise<ApprovalResult>;
  execute: (planId: string, request: Record<string, unknown>) => Promise<AssignmentCommandRecord>;
}

export function tryLockCommandAction(locks: Set<string>, planId: string): boolean {
  if (locks.has(planId)) return false;
  locks.add(planId);
  return true;
}

export async function approveAndExecuteCommand({
  calls,
  planId,
  association,
  onApproved,
  onRunning,
  beforeExecution,
}: {
  calls: CommandLifecycleCalls;
  planId: string;
  association: {
    assignment_id: string;
    workspace_path: string;
    chat_run_id?: string;
  };
  onApproved: (plan: AssignmentCommandRecord) => void;
  onRunning: () => void;
  beforeExecution?: () => Promise<void>;
}): Promise<AssignmentCommandRecord> {
  const approval = await calls.approve(planId, {
    ...association,
    confirmation: `APPROVE ${planId}`,
  });
  onApproved(approval.plan);
  await beforeExecution?.();
  onRunning();
  return calls.execute(planId, {
    ...association,
    approval_token: approval.approval_token,
  });
}

const PYTEST_TOTAL_RE = /(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warning|warnings)\b/g;
const PYTEST_DURATION_RE = /\bin\s+([0-9]+(?:\.[0-9]+)?)s\b/;

export function commandResultPresentation(
  command: AssignmentCommandRecord,
): CommandResultPresentation {
  const output = [command.stdout, command.stderr].filter(Boolean).join("\n");
  const pytestSummary = command.action === "pytest" ? readablePytestSummary(output) : "";
  const succeeded = command.exit_code === 0 && command.display_state === "completed";

  return {
    summary:
      pytestSummary ||
      (succeeded
        ? `Command completed successfully with exit code ${command.exit_code}.`
        : `Command failed with exit code ${command.exit_code ?? "unavailable"}.`),
    errorTail: succeeded ? "" : finalRelevantLines(command.stderr || command.stdout),
  };
}

export function readablePytestSummary(output: string): string {
  const lines = output.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const summaryLine = [...lines].reverse().find((line) =>
    /\b(?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b/.test(line) &&
    PYTEST_DURATION_RE.test(line),
  );
  if (!summaryLine) return "";

  const totals = [...summaryLine.matchAll(PYTEST_TOTAL_RE)].map((match) => ({
    count: Number(match[1]),
    label: match[2],
  }));
  const duration = summaryLine.match(PYTEST_DURATION_RE)?.[1];
  if (!totals.length || !duration) return "";

  if (totals.length === 1 && totals[0].label === "passed") {
    const noun = totals[0].count === 1 ? "test" : "tests";
    return `${totals[0].count} ${noun} passed in ${duration} seconds.`;
  }

  const descriptions = totals.map(({ count, label }) => `${count} ${label}`);
  const joined = descriptions.length === 2
    ? descriptions.join(" and ")
    : `${descriptions.slice(0, -1).join(", ")}, and ${descriptions[descriptions.length - 1]}`;
  return `Pytest finished with ${joined} in ${duration} seconds.`;
}

export function finalRelevantLines(output: string, limit = 8): string {
  return output
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.trim())
    .slice(-limit)
    .join("\n");
}
