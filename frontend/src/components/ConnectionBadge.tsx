import { CircleOff, FlaskConical, Link2 } from "lucide-react";
import type { ConnectionState } from "../types/contracts";

const labels: Record<ConnectionState, string> = {
  connected: "Connected",
  mock: "Mock",
  disabled: "Disabled",
};

export function ConnectionBadge({
  state,
  compact = false,
}: {
  state: ConnectionState;
  compact?: boolean;
}) {
  const Icon =
    state === "connected" ? Link2 : state === "mock" ? FlaskConical : CircleOff;
  return (
    <span
      className={`connection-badge connection-${state} ${
        compact ? "connection-compact" : ""
      }`}
    >
      <Icon size={12} />
      {!compact && labels[state]}
    </span>
  );
}
