import { ChevronDown } from "lucide-react";
import { ollamaCapability } from "../state/localAIState";
import type { LocalAICapabilityReport, LocalAIErrorInfo, LocalAIModelConfiguration } from "../types/localAI";

export function DiagnosticsDrawer({
  capabilities,
  configuredModel,
  error,
  open,
  onToggle,
}: {
  capabilities: LocalAICapabilityReport | null;
  configuredModel: LocalAIModelConfiguration | undefined;
  error: LocalAIErrorInfo | null;
  open: boolean;
  onToggle: () => void;
}) {
  const ollama = ollamaCapability(capabilities);
  return (
    <section className="info-card local-ai-diagnostics-drawer" aria-label="Local AI diagnostics">
      <button
        type="button"
        className="secondary-button local-ai-diagnostics-toggle"
        aria-expanded={open}
        aria-controls="local-ai-diagnostics-panel"
        onClick={onToggle}
      >
        <ChevronDown size={16} className={open ? "chevron-open" : undefined} />
        Diagnostics
      </button>
      {open && (
        <dl id="local-ai-diagnostics-panel" data-testid="local-ai-diagnostics-panel">
          <div><dt>Endpoint</dt><dd>{ollama?.endpoint ?? "Unknown"}</dd></div>
          <div><dt>Installed models</dt><dd>{ollama && ollama.installedModels.length > 0 ? ollama.installedModels.join(", ") : "None"}</dd></div>
          <div><dt>Loaded models</dt><dd>{ollama && ollama.loadedModels.length > 0 ? ollama.loadedModels.join(", ") : "None"}</dd></div>
          <div><dt>Capability snapshot id</dt><dd>{capabilities?.report_id ?? "Unknown"}</dd></div>
          <div><dt>Configuration version</dt><dd>{configuredModel?.configuration_version ?? "Unknown"}</dd></div>
          <div><dt>Last refresh</dt><dd>{capabilities?.generated_at ?? "Never"}</dd></div>
          <div><dt>Provider reachable</dt><dd>{ollama ? (ollama.providerReachable ? "Yes" : "No") : "Unknown"}</dd></div>
          <div><dt>Errors</dt><dd>{error ? error.message : "None"}</dd></div>
        </dl>
      )}
    </section>
  );
}
