import type { LocalAIModelConfiguration } from "../types/localAI";

export function InstalledModelsCard({
  models,
  pendingModelIds,
  onToggle,
}: {
  models: LocalAIModelConfiguration[];
  pendingModelIds: string[];
  onToggle: (model: LocalAIModelConfiguration, enabled: boolean) => void;
}) {
  return (
    <section className="info-card local-ai-models-card" aria-label="Installed local AI models">
      <div className="card-heading">
        <div>
          <span className="eyebrow">Local AI</span>
          <h2>Installed models</h2>
        </div>
      </div>
      {models.length === 0 ? (
        <p>No model profiles were returned by the backend.</p>
      ) : (
        <ul className="local-ai-model-list">
          {models.map((model) => {
            const busy = pendingModelIds.includes(model.model_profile_id);
            return (
              <li key={model.model_profile_id} className="local-ai-model-row" data-model-profile-id={model.model_profile_id}>
                <div className="local-ai-model-identity">
                  <strong>{model.display_name}</strong>
                  <span className="muted">{model.provider_id}</span>
                </div>
                <dl className="local-ai-model-fields">
                  <div><dt>Enabled</dt><dd>{model.enabled ? "Yes" : "No"}</dd></div>
                  <div><dt>Configuration version</dt><dd>{model.configuration_version}</dd></div>
                  <div><dt>Policy status</dt><dd>{model.policy_status.replace(/_/g, " ")}</dd></div>
                  <div><dt>Local availability</dt><dd>{model.local_available ? "Available" : "Unavailable"}</dd></div>
                  <div><dt>Roles</dt><dd>{model.intended_roles.length > 0 ? model.intended_roles.join(", ") : "None declared"}</dd></div>
                </dl>
                <div className="button-row">
                  <button
                    type="button"
                    className={model.enabled ? "secondary-button danger" : "primary-button"}
                    disabled={busy}
                    aria-busy={busy}
                    aria-label={`${model.enabled ? "Disable" : "Enable"} ${model.display_name}`}
                    onClick={() => onToggle(model, !model.enabled)}
                  >
                    {busy ? "Working…" : model.enabled ? "Disable" : "Enable"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
