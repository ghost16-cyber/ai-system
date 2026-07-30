import { RefreshCw } from "lucide-react";
import { summarizeHardware } from "../state/localAIState";
import type { LocalAICapabilityReport } from "../types/localAI";

export function HardwareCard({
  capabilities,
  refreshing,
  onRefresh,
}: {
  capabilities: LocalAICapabilityReport | null;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const hardware = summarizeHardware(capabilities);
  return (
    <section className="info-card local-ai-hardware-card" aria-label="Local AI hardware capabilities">
      <div className="card-heading">
        <div>
          <span className="eyebrow">Local AI</span>
          <h2>Hardware</h2>
        </div>
      </div>
      <dl>
        <div><dt>CPU</dt><dd>{hardware.cpu}</dd></div>
        <div><dt>RAM</dt><dd>{hardware.memory}</dd></div>
        <div><dt>GPU</dt><dd>{hardware.gpu}</dd></div>
        <div><dt>CUDA</dt><dd>{hardware.cuda}</dd></div>
        <div><dt>VRAM</dt><dd>{hardware.vram}</dd></div>
        <div><dt>Provider</dt><dd>{hardware.provider}</dd></div>
      </dl>
      <div className="button-row">
        <button
          type="button"
          className="secondary-button"
          disabled={refreshing}
          aria-busy={refreshing}
          onClick={onRefresh}
        >
          <RefreshCw size={16} className={refreshing ? "spin" : undefined} />
          {refreshing ? "Refreshing…" : "Refresh Capabilities"}
        </button>
      </div>
    </section>
  );
}
