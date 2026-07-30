import { classifyOverallStatus, overallStatusLabel } from "../state/localAIState";
import type { LocalAIModelConfiguration } from "../types/localAI";

export function LocalAIStatusCard({
  model,
  loading,
}: {
  model: LocalAIModelConfiguration | undefined;
  loading: boolean;
}) {
  const status = classifyOverallStatus(model);
  const label = loading ? "Loading" : overallStatusLabel(status);
  return (
    <section className="info-card local-ai-status-card" aria-label="Local AI overall status">
      <div className="card-heading">
        <div>
          <span className="eyebrow">Local AI</span>
          <h2>Overall status</h2>
        </div>
        <span
          className={`status local-ai-status-${loading ? "loading" : status}`}
          aria-live="polite"
        >
          {label}
        </span>
      </div>
      {model ? (
        <p>
          {model.display_name} via {model.provider_id}
          {model.enabled ? " is enabled." : " is installed but not enabled."}
        </p>
      ) : (
        <p>No configured local model profile was found in the backend response.</p>
      )}
    </section>
  );
}
