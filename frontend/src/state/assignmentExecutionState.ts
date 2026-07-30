import type { AssignmentExecutionDisplayState } from "../clients/astraClient";

export function mapAssignmentExecutionState(status: string): AssignmentExecutionDisplayState {
  switch (status) {
    case "planned": return "pending";
    case "approved": return "approved";
    case "running": return "running";
    case "succeeded": return "completed";
    case "approval_expired": return "expired";
    case "failed":
    case "timed_out":
    default: return "failed";
  }
}
