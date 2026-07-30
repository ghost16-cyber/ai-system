import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  FileText,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { HttpAstraClient } from "../clients/astraClient";
import type {
  SpecialistBenchmark,
  SpecialistDashboard,
  SpecialistModel,
  SpecialistModelsResponse,
  SpecialistRouteResult,
  SpecialistTracesResponse,
} from "../types/contracts";
import { ConnectionBadge } from "./ConnectionBadge";

type DetailPanel = { title: string; body: Record<string, unknown> } | null;

export function SpecialistsView() {
  const httpClient = useMemo(
    () => new HttpAstraClient(),
    [],
  );
  const [dashboard, setDashboard] = useState<SpecialistDashboard | null>(null);
  const [models, setModels] = useState<SpecialistModelsResponse | null>(null);
  const [traces, setTraces] = useState<SpecialistTracesResponse | null>(null);
  const [benchmark, setBenchmark] = useState<SpecialistBenchmark | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [routeText, setRouteText] = useState("CUDA out of memory while building a RAG index");
  const [useSlmIntent, setUseSlmIntent] = useState(false);
  const [routeResult, setRouteResult] = useState<SpecialistRouteResult | null>(null);
  const [detailPanel, setDetailPanel] = useState<DetailPanel>(null);

  const loadData = useCallback(async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [nextDashboard, nextModels, nextTraces, nextBenchmark] =
        await Promise.all([
          httpClient.getSpecialistDashboard(),
          httpClient.getSpecialistModels(),
          httpClient.getSpecialistTraces(),
          httpClient.getSpecialistRouterBenchmark(),
        ]);
      setDashboard(nextDashboard);
      setModels(nextModels);
      setTraces(nextTraces);
      setBenchmark(nextBenchmark);
    } catch (caught) {
      setError(cleanError(caught));
    } finally {
      setLoading(false);
    }
  }, [httpClient]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function runRouteTest() {
    setError(null);
    try {
      setRouteResult(await httpClient.routeSpecialistTask(routeText, useSlmIntent));
      await loadData();
    } catch (caught) {
      setError(cleanError(caught));
    }
  }

  async function runLifecycleAction(
    model: SpecialistModel,
    action: "promote" | "deactivate" | "reject" | "rollback",
  ) {
    if (
      ["deactivate", "reject", "rollback"].includes(action) &&
      !window.confirm(`Confirm ${action} for ${model.model_id}?`)
    ) {
      return;
    }
    setActionBusy(`${action}:${model.model_id}`);
    setError(null);
    try {
      await httpClient.runSpecialistModelAction(model.model_id, action);
      await loadData();
    } catch (caught) {
      setError(cleanError(caught));
    } finally {
      setActionBusy(null);
    }
  }

  async function openDetail(model: SpecialistModel, kind: "report" | "audit") {
    setError(null);
    try {
      const body =
        kind === "report"
          ? await httpClient.getSpecialistModelReport(model.model_id)
          : await httpClient.getSpecialistModelAudit(model.model_id);
      setDetailPanel({ title: `${kind} / ${model.model_id}`, body });
    } catch (caught) {
      setError(cleanError(caught));
    }
  }

  return (
    <div className="page-stack specialists-page">
      <section className="page-intro">
        <div>
          <span className="eyebrow">Specialists</span>
          <h2>Recommendation-only specialist control plane</h2>
          <p>
            Dashboard, traces, router benchmarks, and lifecycle controls are backed by
            backend state. Specialist outputs never execute tools, apply patches, or
            authorize runtime actions.
          </p>
        </div>
        <div className="specialist-header-actions">
          <ConnectionBadge state={error ? "disabled" : "connected"} />
          <button className="secondary-button" onClick={loadData}>
            <RefreshCw size={15} /> Refresh
          </button>
        </div>
      </section>

      {error && <Notice tone="amber" title="Backend fallback active" detail={error} />}
      {loading && <Notice tone="blue" title="Loading specialist data" detail="Fetching dashboard, models, traces, and benchmark." />}

      <section className="specialist-grid">
        <StatusCard title="Models" counts={dashboard?.models_by_status} keys={["candidate", "promoted", "rejected", "deactivated"]} />
        <StatusCard title="Datasets" counts={dashboard?.datasets_by_status} keys={["uploaded", "validated", "approved", "rejected", "archived"]} />
        <StatusCard title="Training jobs" counts={dashboard?.training_jobs_by_status} keys={["queued", "running", "completed", "failed", "rejected"]} />
        <section className="data-section">
          <SectionTitle icon={ShieldCheck} title="Fallback status" />
          <div className="fallback-panel">
            <strong>{String(dashboard?.fallback_status?.rule_based_fallback_available ?? true)}</strong>
            <span>Rule-based fallback available</span>
            <small>
              Promoted models: {String(dashboard?.fallback_status?.promoted_model_count ?? 0)}
            </small>
          </div>
        </section>
      </section>

      <div className="specialist-main-grid">
        <section className="data-section router-panel">
          <SectionTitle icon={Activity} title="Route test" />
          <textarea
            value={routeText}
            onChange={(event) => setRouteText(event.target.value)}
            aria-label="Specialist route test text"
          />
          <label className="inline-check">
            <input
              type="checkbox"
              checked={useSlmIntent}
              onChange={(event) => setUseSlmIntent(event.target.checked)}
            />
            Use SLM intent as advisory context
          </label>
          <button className="primary-button" onClick={runRouteTest} disabled={!routeText.trim()}>
            Test route
          </button>
          {routeResult ? (
            <div className="route-result">
              <Signal term="Task type" value={routeResult.task_type} />
              <Signal term="Specialist" value={routeResult.recommended_specialist} />
              <Signal term="Confidence" value={`${Math.round(routeResult.confidence * 100)}%`} />
              <Signal term="Promoted model" value={routeResult.promoted_model_available ? "available" : "not available"} />
              <Signal term="Model ID" value={routeResult.model_id ?? "fallback"} />
              <Signal term="Fallback required" value={routeResult.fallback_required ? "yes" : "no"} />
              <Signal term="SLM advisory" value={routeResult.slm_intent_used ? "used" : "not used"} />
              <ul className="safety-note-list">
                {routeResult.safety_notes.map((note) => (
                  <li key={note}>
                    <ShieldCheck size={14} /> {note}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <EmptyState text="Run a route test to see the deterministic specialist recommendation." />
          )}
        </section>

        <section className="data-section">
          <SectionTitle icon={BarChart3} title="Router benchmark" />
          {benchmark ? (
            <div className="benchmark-panel">
              <strong>{Math.round(benchmark.overall_accuracy * 100)}%</strong>
              <span>{benchmark.correct} / {benchmark.total_examples} examples correct</span>
              <div className="benchmark-tasks">
                {Object.entries(benchmark.accuracy_by_task_type).map(([task, value]) => (
                  <Signal key={task} term={task} value={`${Math.round(value.accuracy * 100)}%`} />
                ))}
              </div>
            </div>
          ) : (
            <EmptyState text="Router benchmark is not loaded yet." />
          )}
        </section>
      </div>

      <section className="data-section">
        <SectionTitle icon={FileText} title="Model lifecycle" />
        {models?.models.length ? (
          <div className="specialist-table">
            {models.models.map((model) => (
              <ModelRow
                key={model.model_id}
                model={model}
                busy={actionBusy?.endsWith(model.model_id) ?? false}
                onAction={runLifecycleAction}
                onDetail={openDetail}
              />
            ))}
          </div>
        ) : (
          <EmptyState text="No specialist models registered yet." />
        )}
      </section>

      <div className="specialist-main-grid">
        <ListPanel title="Recent traces" items={traces?.traces ?? dashboard?.recent_traces ?? []} empty="No specialist traces yet." />
        <ListPanel title="Recent audit events" items={dashboard?.recent_audit_events ?? []} empty="No model audit events yet." />
      </div>

      {detailPanel && (
        <section className="data-section detail-json-panel">
          <div className="detail-title-row">
            <div>
              <span className="eyebrow">Read-only detail</span>
              <h2>{detailPanel.title}</h2>
            </div>
            <button className="secondary-button" onClick={() => setDetailPanel(null)}>
              Close
            </button>
          </div>
          <pre className="code-preview">
            <code>{JSON.stringify(detailPanel.body, null, 2)}</code>
          </pre>
        </section>
      )}
    </div>
  );
}

function StatusCard({ title, counts, keys }: { title: string; counts?: Record<string, number>; keys: string[] }) {
  return (
    <section className="data-section status-card">
      <h3>{title}</h3>
      <div className="status-badge-grid">
        {keys.map((key) => (
          <span className={`lifecycle-badge status-${key}`} key={key}>
            {key} <strong>{counts?.[key] ?? 0}</strong>
          </span>
        ))}
      </div>
    </section>
  );
}

function ModelRow({
  model,
  busy,
  onAction,
  onDetail,
}: {
  model: SpecialistModel;
  busy: boolean;
  onAction: (model: SpecialistModel, action: "promote" | "deactivate" | "reject" | "rollback") => void;
  onDetail: (model: SpecialistModel, kind: "report" | "audit") => void;
}) {
  const metrics = metricSummary(model);
  return (
    <div className="specialist-model-row">
      <div>
        <strong>{model.model_id}</strong>
        <span>{model.specialist}</span>
      </div>
      <span className={`lifecycle-badge status-${model.lifecycle_status}`}>
        {model.lifecycle_status}
      </span>
      <span>{metrics}</span>
      <span>{String(model.metadata?.created_at ?? model.created_at ?? "unknown")}</span>
      <div className="model-read-actions">
        <button className="secondary-button" onClick={() => onDetail(model, "report")}>Report</button>
        <button className="secondary-button" onClick={() => onDetail(model, "audit")}>Audit</button>
      </div>
      <div className="model-life-actions">
        {model.lifecycle_status === "candidate" && (
          <>
            <button className="primary-button" disabled={busy} onClick={() => onAction(model, "promote")}>Promote</button>
            <button className="secondary-button danger-action" disabled={busy} onClick={() => onAction(model, "reject")}>Reject</button>
          </>
        )}
        {model.lifecycle_status === "promoted" && (
          <button className="secondary-button danger-action" disabled={busy} onClick={() => onAction(model, "deactivate")}>Deactivate</button>
        )}
        {model.lifecycle_status !== "promoted" && model.lifecycle_status !== "rejected" && (
          <button className="secondary-button" disabled={busy} onClick={() => onAction(model, "rollback")}>
            <RotateCcw size={14} /> Rollback
          </button>
        )}
      </div>
    </div>
  );
}

function metricSummary(model: SpecialistModel) {
  const metrics = model.metadata?.metrics;
  if (!metrics || typeof metrics !== "object") return "metrics unavailable";
  return ["accuracy", "precision", "recall", "f1_score"]
    .map((key) => {
      const value = (metrics as Record<string, unknown>)[key];
      return typeof value === "number" ? `${key}: ${value.toFixed(2)}` : null;
    })
    .filter(Boolean)
    .join(" / ") || "metrics unavailable";
}

function ListPanel({ title, items, empty }: { title: string; items: Array<Record<string, unknown>>; empty: string }) {
  return (
    <section className="data-section">
      <SectionTitle icon={CheckCircle2} title={title} />
      {items.length ? (
        <div className="compact-record-list">
          {items.slice(0, 8).map((item, index) => (
            <div className="compact-record" key={`${title}-${index}`}>
              <strong>{String(item.action ?? item.recommended_specialist ?? item.trace_id ?? "event")}</strong>
              <span>{String(item.timestamp ?? item.model_id ?? item.decision_source ?? "latest")}</span>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState text={empty} />
      )}
    </section>
  );
}

function Notice({ tone, title, detail }: { tone: "amber" | "blue"; title: string; detail: string }) {
  return (
    <div className={`specialist-notice notice-${tone}`}>
      <AlertTriangle size={16} />
      <span>
        <strong>{title}</strong>
        <small>{detail}</small>
      </span>
    </div>
  );
}

function SectionTitle({ icon: Icon, title }: { icon: typeof Activity; title: string }) {
  return (
    <div className="section-heading compact">
      <div className="section-title-inline">
        <Icon size={17} />
        <h2>{title}</h2>
      </div>
    </div>
  );
}

function Signal({ term, value }: { term: string; value: string }) {
  return (
    <div className="specialist-signal">
      <span>{term}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="specialist-empty">{text}</div>;
}

function cleanError(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
