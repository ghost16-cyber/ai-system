import { X } from "lucide-react";
import type { AstraClient } from "../../../clients/astraClient";
import { useLocalAI } from "../hooks/useLocalAI";
import { diagnoseLocalAvailability, findConfiguredModel } from "../state/localAIState";
import { DiagnosticsDrawer } from "./DiagnosticsDrawer";
import { HardwareCard } from "./HardwareCard";
import { InstalledModelsCard } from "./InstalledModelsCard";
import { LocalAIStatusCard } from "./LocalAIStatusCard";

export function LocalAIPage({
  client,
  actorId,
  onClose,
}: {
  client: AstraClient;
  actorId: string;
  onClose: () => void;
}) {
  const { state, refresh, setModelEnabled, toggleDiagnostics, dismissError } = useLocalAI(client, actorId);
  const configuredModel = findConfiguredModel(state.models);
  const initialLoad = state.models.length === 0 && (state.status === "idle" || state.status === "loading");
  const initialLoadFailed = state.models.length === 0 && state.status === "error";

  return (
    <div className="local-ai-overlay" role="presentation" onClick={onClose}>
      <div
        className="local-ai-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Local AI control centre"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="local-ai-panel-header">
          <h1>Local AI</h1>
          <button type="button" className="secondary-button" aria-label="Close Local AI settings" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        {initialLoad && (
          <p className="local-ai-loading" role="status">Loading Local AI…</p>
        )}
        {initialLoadFailed && (
          <div className="local-ai-error" role="alert">
            <span>{state.error?.message}</span>
            <button type="button" className="secondary-button" onClick={() => void refresh()}>Try again</button>
          </div>
        )}
        {!initialLoad && !initialLoadFailed && (
          <div className="local-ai-body">
            {state.error && (
              <div className="local-ai-error" role="alert">
                <div>
                  <span>{state.error.message}</span>
                  {state.error.kind === "model_unavailable" && (
                    <span className="local-ai-error-diagnosis">
                      {diagnoseLocalAvailability(state.capabilities)}
                    </span>
                  )}
                </div>
                <button type="button" className="secondary-button" aria-label="Dismiss error" onClick={dismissError}>
                  Dismiss
                </button>
              </div>
            )}
            <LocalAIStatusCard model={configuredModel} loading={state.status === "loading"} />
            <InstalledModelsCard
              models={state.models}
              pendingModelIds={state.pendingModelIds}
              onToggle={(model, enabled) => void setModelEnabled(model, enabled)}
            />
            <HardwareCard
              capabilities={state.capabilities}
              refreshing={state.refreshing}
              onRefresh={() => void refresh()}
            />
            <DiagnosticsDrawer
              capabilities={state.capabilities}
              configuredModel={configuredModel}
              error={state.error}
              open={state.diagnosticsOpen}
              onToggle={toggleDiagnostics}
            />
          </div>
        )}
      </div>
    </div>
  );
}
