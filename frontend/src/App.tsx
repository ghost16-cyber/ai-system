import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  History,
  MessageSquareText,
  RefreshCw,
  RotateCcw,
  Send,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Wrench,
  Zap,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  HttpAstraClient,
  type HealthData,
  type RagStatusResponse,
  type RawHistoryItem,
  type RawJob,
  type RawTool,
  type SelectedSlmResponse,
  type SlmChatResponse,
  type SlmProfilesResponse,
} from "./clients/astraClient";
import type {
  CompactTraceResponse,
  ExecutionProfile,
  PlanDecision,
  RuntimeContext,
  RuntimePlanValidation,
  SpecialistDashboard,
  SpecialistModelsResponse,
  SpecialistRouteResult,
  SpecialistTracesResponse,
  TaskKind,
  TraceEvent,
} from "./types/contracts";

type PageId = "chat" | "system" | "history" | "settings";
type SafetyMode = "read_only" | "confirm";

interface FrontendSettings {
  apiUrl: string;
  slmProfileId: string;
  ragEnabled: boolean;
  specialistRoutingEnabled: boolean;
  safetyMode: SafetyMode;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  createdAt: string;
  meta?: ChatRun;
}

interface ChatRun {
  id: string;
  title: string;
  createdAt: string;
  userMessage: string;
  assistantMessage: string;
  selectedSpecialist: string;
  runtimeProfile: string;
  safetyDecision: PlanDecision | "unknown";
  traceId: string | null;
  timeline: TraceEvent[];
  validationReason: string;
}

interface SystemData {
  health: HealthData | null;
  runtime: RuntimeContext | null;
  selectedSlm: SelectedSlmResponse | null;
  slmProfiles: SlmProfilesResponse | null;
  rag: RagStatusResponse | null;
  specialistDashboard: SpecialistDashboard | null;
  specialistModels: SpecialistModelsResponse | null;
  tools: RawTool[];
}

interface HistoryData {
  jobs: RawJob[];
  analyses: RawHistoryItem[];
  specialistTraces: SpecialistTracesResponse | null;
}

const SETTINGS_KEY = "astra.phase48.settings";
const CHAT_HISTORY_KEY = "astra.phase48.chatRuns";

const defaultSettings: FrontendSettings = {
  apiUrl: "http://127.0.0.1:8000",
  slmProfileId: "",
  ragEnabled: true,
  specialistRoutingEnabled: true,
  safetyMode: "read_only",
};

const pages: Array<{ id: PageId; label: string; icon: typeof MessageSquareText }> = [
  { id: "chat", label: "Chat", icon: MessageSquareText },
  { id: "system", label: "System", icon: Cpu },
  { id: "history", label: "History", icon: History },
  { id: "settings", label: "Settings", icon: Settings },
];

function App() {
  const [activePage, setActivePage] = useState<PageId>("chat");
  const [settings, setSettings] = useState<FrontendSettings>(loadSettings);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [runs, setRuns] = useState<ChatRun[]>(loadRuns);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);
  const [systemData, setSystemData] = useState<SystemData | null>(null);
  const [systemLoading, setSystemLoading] = useState(true);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [historyData, setHistoryData] = useState<HistoryData | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<CompactTraceResponse | null>(null);
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null);

  const client = useMemo(
    () => new HttpAstraClient(settings.apiUrl),
    [settings.apiUrl],
  );

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(runs.slice(0, 30)));
  }, [runs]);

  const refreshSystem = useCallback(async () => {
    setSystemLoading(true);
    setSystemError(null);
    const [
      health,
      runtime,
      selectedSlm,
      slmProfiles,
      rag,
      specialistDashboard,
      specialistModels,
      tools,
    ] = await Promise.all([
      settle(client.getHealth()),
      settle(client.getRuntimeContext()),
      settle(client.getSelectedSlm()),
      settle(client.getSlmProfiles()),
      settle(client.getRagStatus()),
      settle(client.getSpecialistDashboard()),
      settle(client.getSpecialistModels()),
      settle(client.getTools()),
    ]);

    setSystemData({
      health: health.value,
      runtime: runtime.value,
      selectedSlm: selectedSlm.value,
      slmProfiles: slmProfiles.value,
      rag: rag.value,
      specialistDashboard: specialistDashboard.value,
      specialistModels: specialistModels.value,
      tools: tools.value ?? [],
    });

    const firstError = [
      health.error,
      runtime.error,
      selectedSlm.error,
      slmProfiles.error,
      rag.error,
      specialistDashboard.error,
      specialistModels.error,
      tools.error,
    ].find(Boolean);
    setSystemError(firstError ?? null);
    setSystemLoading(false);
  }, [client]);

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    const [jobs, analyses, traces] = await Promise.all([
      settle(client.getJobs(30)),
      settle(client.getHistory(30)),
      settle(client.getSpecialistTraces()),
    ]);
    setHistoryData({
      jobs: jobs.value ?? [],
      analyses: analyses.value ?? [],
      specialistTraces: traces.value,
    });
    setHistoryError(jobs.error ?? analyses.error ?? traces.error ?? null);
    setHistoryLoading(false);
  }, [client]);

  useEffect(() => {
    void refreshSystem();
    void refreshHistory();
  }, [refreshSystem, refreshHistory]);

  useEffect(() => {
    if (!settings.slmProfileId && systemData?.selectedSlm?.selected_profile_id) {
      setSettings((current) => ({
        ...current,
        slmProfileId: systemData.selectedSlm?.selected_profile_id ?? "",
      }));
    }
  }, [settings.slmProfileId, systemData?.selectedSlm?.selected_profile_id]);

  useEffect(() => {
    if (!selectedRunId) {
      setSelectedTrace(null);
      return;
    }
    if (!selectedRunId.startsWith("job:")) {
      setSelectedTrace(null);
      return;
    }
    const jobId = selectedRunId.slice(4);
    let cancelled = false;
    client
      .getCompactTrace(jobId)
      .then((trace) => {
        if (!cancelled) setSelectedTrace(trace);
      })
      .catch(() => {
        if (!cancelled) setSelectedTrace(null);
      });
    return () => {
      cancelled = true;
    };
  }, [client, selectedRunId]);

  async function sendChat(promptOverride?: string) {
    const prompt = (promptOverride ?? chatInput).trim();
    if (!prompt || chatLoading) return;

    if (looksDestructive(prompt)) {
      const confirmed = window.confirm(
        "This request sounds destructive. Chat is read-only, and Astra will only produce a preview unless you explicitly authorize an action elsewhere. Continue?",
      );
      if (!confirmed) return;
    }

    setChatInput("");
    setLastPrompt(prompt);
    setChatError(null);
    setChatLoading(true);
    const userMessage: ChatMessage = {
      id: newId("user"),
      role: "user",
      text: prompt,
      createdAt: new Date().toISOString(),
    };
    setMessages((current) => [...current, userMessage]);

    try {
      const taskKind = inferTaskKind(prompt);
      const requestedPlan = defaultPlanForTaskKind(taskKind);
      const [routeResult, validation] = await Promise.all([
        settings.specialistRoutingEnabled
          ? settle(client.routeSpecialistTask(prompt, false))
          : Promise.resolve({ value: null, error: null }),
        settle(
          client.validateRuntimePlan({
            task: prompt,
            taskKind,
            requestedPlan,
          }),
        ),
      ]);

      if (!validation.value) {
        throw new Error(validation.error ?? "Runtime validation failed.");
      }

      const profile = validation.value.decision === "block"
        ? null
        : await client
            .buildExecutionProfile({
              task: prompt,
              taskKind,
              requestedPlan: validation.value.recommendedPlan,
            })
            .catch(() => null);

      const chatResponse = settings.ragEnabled
        ? await client.chatWithContext(prompt)
        : await client.chatWithSlm(prompt, {
            safety_mode: settings.safetyMode,
            specialist: routeResult.value?.recommended_specialist,
          });

      const assistantText =
        readString(chatResponse.assistant_response) ||
        "Astra completed the request, but the backend did not return response text.";
      const run = buildChatRun({
        prompt,
        assistantText,
        route: routeResult.value,
        validation: validation.value,
        profile,
        response: chatResponse,
      });
      const assistantMessage: ChatMessage = {
        id: newId("assistant"),
        role: "assistant",
        text: assistantText,
        createdAt: run.createdAt,
        meta: run,
      };
      setMessages((current) => [...current, assistantMessage]);
      setRuns((current) => [run, ...current].slice(0, 30));
      setSelectedRunId(`chat:${run.id}`);
      void refreshHistory();
    } catch (error) {
      const message = cleanError(error);
      setChatError(message);
      setMessages((current) => [
        ...current,
        {
          id: newId("assistant-error"),
          role: "assistant",
          text: `I could not reach the live backend: ${message}`,
          createdAt: new Date().toISOString(),
        },
      ]);
    } finally {
      setChatLoading(false);
    }
  }

  function resetLocalState() {
    localStorage.removeItem(CHAT_HISTORY_KEY);
    localStorage.removeItem(SETTINGS_KEY);
    setRuns([]);
    setMessages([]);
    setSelectedRunId(null);
    setSettings(defaultSettings);
    setSettingsNotice("Local chat history and frontend settings were reset.");
  }

  return (
    <div className="prototype-shell">
      <header className="app-header">
        <div className="brand-lockup">
          <span className="brand-glyph">
            <Sparkles size={18} />
          </span>
          <div>
            <strong>Astra</strong>
            <span>Prototype assistant</span>
          </div>
        </div>
        <nav className="simple-nav" aria-label="Primary navigation">
          {pages.map((page) => {
            const Icon = page.icon;
            return (
              <button
                key={page.id}
                className={activePage === page.id ? "active" : ""}
                onClick={() => setActivePage(page.id)}
              >
                <Icon size={16} />
                {page.label}
              </button>
            );
          })}
        </nav>
        <StatusPill
          label={systemData?.health?.status === "ok" ? "Backend online" : "Backend offline"}
          tone={systemData?.health?.status === "ok" ? "green" : "red"}
        />
      </header>

      <main className="prototype-main">
        {activePage === "chat" && (
          <ChatPage
            messages={messages}
            input={chatInput}
            setInput={setChatInput}
            loading={chatLoading}
            error={chatError}
            lastPrompt={lastPrompt}
            onSubmit={() => void sendChat()}
            onRetry={() => lastPrompt && void sendChat(lastPrompt)}
            settings={settings}
            runtime={systemData?.runtime ?? null}
          />
        )}
        {activePage === "system" && (
          <SystemPage
            data={systemData}
            loading={systemLoading}
            error={systemError}
            settings={settings}
            onRefresh={() => void refreshSystem()}
          />
        )}
        {activePage === "history" && (
          <HistoryPage
            runs={runs}
            data={historyData}
            loading={historyLoading}
            error={historyError}
            selectedRunId={selectedRunId}
            setSelectedRunId={setSelectedRunId}
            selectedTrace={selectedTrace}
            onRefresh={() => void refreshHistory()}
          />
        )}
        {activePage === "settings" && (
          <SettingsPage
            settings={settings}
            setSettings={setSettings}
            client={client}
            slmProfiles={systemData?.slmProfiles ?? null}
            notice={settingsNotice}
            setNotice={setSettingsNotice}
            onReset={resetLocalState}
            onRefreshSystem={() => void refreshSystem()}
          />
        )}
      </main>
    </div>
  );
}

function ChatPage({
  messages,
  input,
  setInput,
  loading,
  error,
  lastPrompt,
  onSubmit,
  onRetry,
  settings,
  runtime,
}: {
  messages: ChatMessage[];
  input: string;
  setInput: (value: string) => void;
  loading: boolean;
  error: string | null;
  lastPrompt: string | null;
  onSubmit: () => void;
  onRetry: () => void;
  settings: FrontendSettings;
  runtime: RuntimeContext | null;
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <section className="page-grid chat-grid">
      <div className="chat-panel">
        <PageTitle
          eyebrow="Chat"
          title="Ask Astra"
          detail="Live backend calls only. Chat runs in preview mode and does not apply patches, delete files, or execute destructive actions."
        />
        <div className="message-list">
          {messages.length === 0 ? (
            <EmptyState
              icon={MessageSquareText}
              title="No messages yet"
              detail="Send a task or question to get a live backend response, specialist routing, runtime profile, and safety decision."
            />
          ) : (
            messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <div className="message-avatar">
                  {message.role === "user" ? "You" : "AI"}
                </div>
                <div className="message-body">
                  <p>{message.text}</p>
                  {message.meta && <ChatResultMeta run={message.meta} />}
                </div>
              </article>
            ))
          )}
          {loading && (
            <div className="message assistant">
              <div className="message-avatar">AI</div>
              <div className="message-body loading-row">
                <Activity size={16} className="spin" />
                Calling backend, routing specialist, checking safety...
              </div>
            </div>
          )}
        </div>
        {error && (
          <div className="notice error">
            <AlertTriangle size={16} />
            <span>{error}</span>
            {lastPrompt && (
              <button className="ghost-button" onClick={onRetry} disabled={loading}>
                Retry
              </button>
            )}
          </div>
        )}
        <form className="chat-composer" onSubmit={submit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask Astra to inspect, explain, plan, or draft a safe next step..."
            aria-label="Message Astra"
          />
          <button className="primary-button" disabled={loading || !input.trim()}>
            {loading ? <Activity size={16} className="spin" /> : <Send size={16} />}
            Send
          </button>
        </form>
      </div>
      <aside className="context-panel">
        <PanelTitle icon={ShieldCheck} title="Current guardrails" />
        <InfoList
          items={[
            ["Safety mode", labelSafety(settings.safetyMode)],
            ["RAG", settings.ragEnabled ? "Enabled" : "Disabled"],
            ["Specialist routing", settings.specialistRoutingEnabled ? "Enabled" : "Disabled"],
            ["Runtime", runtime ? `${runtime.machine.gpu || "CPU"} / ${runtime.machine.ramGb} GB RAM` : "Unavailable"],
          ]}
        />
        <div className="notice subtle">
          <ShieldCheck size={16} />
          Chat is read-only. Destructive requests require explicit confirmation and still produce previews only.
        </div>
      </aside>
    </section>
  );
}

function ChatResultMeta({ run }: { run: ChatRun }) {
  return (
    <div className="result-meta">
      <Metric label="Specialist" value={run.selectedSpecialist} />
      <Metric label="Runtime profile" value={run.runtimeProfile} />
      <Metric label="Safety decision" value={run.safetyDecision} tone={decisionTone(run.safetyDecision)} />
      <Metric label="Trace ID" value={run.traceId ?? "Not returned"} />
      <p className="meta-reason">{run.validationReason}</p>
    </div>
  );
}

function SystemPage({
  data,
  loading,
  error,
  settings,
  onRefresh,
}: {
  data: SystemData | null;
  loading: boolean;
  error: string | null;
  settings: FrontendSettings;
  onRefresh: () => void;
}) {
  const runtime = data?.runtime;
  const selectedProfile = data?.selectedSlm?.profile ?? {};
  const modelCounts = data?.specialistDashboard?.models_by_status ?? {};

  return (
    <section className="page-stack">
      <PageToolbar
        eyebrow="System"
        title="Runtime and backend status"
        detail="Dashboard, runtime, specialists, and profiles are merged into this compact system view."
        loading={loading}
        onRefresh={onRefresh}
      />
      {error && <Notice tone="amber" text={`Some backend data could not load: ${error}`} />}
      <div className="overview-grid">
        <StatusCard
          icon={Database}
          title="Backend"
          value={data?.health?.status === "ok" ? "Online" : "Offline"}
          detail={data?.health ? `${data.health.version} / ${data.health.phase}` : "No health response"}
          tone={data?.health?.status === "ok" ? "green" : "red"}
        />
        <StatusCard
          icon={Zap}
          title="GPU"
          value={runtime?.machine.gpu || "CPU only"}
          detail={runtime ? `${runtime.machine.vramGb} GB VRAM / CUDA ${runtime.machine.cudaAvailable ? "yes" : "no"}` : "Runtime unavailable"}
        />
        <StatusCard
          icon={Cpu}
          title="CPU / RAM"
          value={runtime?.machine.cpu || "Unknown CPU"}
          detail={runtime ? `${runtime.machine.logicalCores} cores / ${runtime.machine.ramGb} GB RAM` : "Runtime unavailable"}
        />
        <StatusCard
          icon={ShieldCheck}
          title="Safety"
          value={labelSafety(settings.safetyMode)}
          detail={runtime?.policy.lowVramMode ? "Low-VRAM policy enabled" : "Standard runtime policy"}
          tone="amber"
        />
      </div>
      <div className="two-column">
        <section className="panel">
          <PanelTitle icon={Bot} title="SLM and RAG" />
          <InfoList
            items={[
              ["Selected SLM", readString(selectedProfile.name) || data?.selectedSlm?.selected_profile_id || "Unavailable"],
              ["Backend", readString(selectedProfile.backend) || "Unknown"],
              ["Profiles", data?.slmProfiles ? String(data.slmProfiles.count) : "Unavailable"],
              ["RAG status", data?.rag?.status ?? "Unavailable"],
              ["Indexed files", data?.rag ? String(data.rag.indexed_file_count) : "Unavailable"],
            ]}
          />
        </section>
        <section className="panel">
          <PanelTitle icon={SlidersHorizontal} title="Specialists" />
          <InfoList
            items={[
              ["Models", data?.specialistModels ? String(data.specialistModels.count) : "Unavailable"],
              ["Promoted", String(modelCounts.promoted ?? 0)],
              ["Candidates", String(modelCounts.candidate ?? 0)],
              ["Read-only", data?.specialistDashboard?.read_only ? "Yes" : "No"],
              ["Routing setting", settings.specialistRoutingEnabled ? "Enabled" : "Disabled"],
            ]}
          />
        </section>
      </div>
      <section className="panel">
        <PanelTitle icon={Wrench} title="Registered tools" />
        {data?.tools.length ? (
          <div className="tool-grid">
            {data.tools.map((tool) => (
              <div className="tool-chip" key={tool.name}>
                <strong>{tool.name}</strong>
                <span>{tool.read_only ? "Read-only" : "Action"} / {tool.execution}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyInline text="No tools loaded." />
        )}
      </section>
    </section>
  );
}

function HistoryPage({
  runs,
  data,
  loading,
  error,
  selectedRunId,
  setSelectedRunId,
  selectedTrace,
  onRefresh,
}: {
  runs: ChatRun[];
  data: HistoryData | null;
  loading: boolean;
  error: string | null;
  selectedRunId: string | null;
  setSelectedRunId: (id: string) => void;
  selectedTrace: CompactTraceResponse | null;
  onRefresh: () => void;
}) {
  const items = buildHistoryItems(runs, data);
  const selected = items.find((item) => item.id === selectedRunId) ?? items[0] ?? null;

  useEffect(() => {
    if (!selectedRunId && items[0]) setSelectedRunId(items[0].id);
  }, [items, selectedRunId, setSelectedRunId]);

  return (
    <section className="page-stack">
      <PageToolbar
        eyebrow="History"
        title="Runs, traces, and audit"
        detail="Recent conversations, jobs, analyses, and specialist trace events in one place."
        loading={loading}
        onRefresh={onRefresh}
      />
      {error && <Notice tone="amber" text={`History is partially unavailable: ${error}`} />}
      {items.length === 0 ? (
        <EmptyState
          icon={History}
          title="No runs yet"
          detail="Send a message in Chat or run backend jobs to populate history."
        />
      ) : (
        <div className="history-layout">
          <div className="history-list">
            {items.map((item) => (
              <button
                key={item.id}
                className={selected?.id === item.id ? "history-item selected" : "history-item"}
                onClick={() => setSelectedRunId(item.id)}
              >
                <strong>{item.title}</strong>
                <span>{item.kind} / {item.meta}</span>
                <small>{formatAge(item.createdAt)}</small>
              </button>
            ))}
          </div>
          <section className="panel history-detail">
            <PanelTitle icon={Clock3} title={selected?.title ?? "Run detail"} />
            {selected ? (
              <>
                <InfoList
                  items={[
                    ["Type", selected.kind],
                    ["Status", selected.meta],
                    ["Created", formatDate(selected.createdAt)],
                    ["Decision", selected.decision ?? "Unavailable"],
                  ]}
                />
                <h3>Trace timeline</h3>
                <TraceTimeline
                  events={
                    selected.kind === "Chat"
                      ? runs.find((run) => `chat:${run.id}` === selected.id)?.timeline ?? []
                      : selectedTrace?.trace ?? []
                  }
                />
                {selected.detail && <pre className="json-preview">{selected.detail}</pre>}
              </>
            ) : (
              <EmptyInline text="Select a run to inspect details." />
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function SettingsPage({
  settings,
  setSettings,
  client,
  slmProfiles,
  notice,
  setNotice,
  onReset,
  onRefreshSystem,
}: {
  settings: FrontendSettings;
  setSettings: React.Dispatch<React.SetStateAction<FrontendSettings>>;
  client: HttpAstraClient;
  slmProfiles: SlmProfilesResponse | null;
  notice: string | null;
  setNotice: (notice: string | null) => void;
  onReset: () => void;
  onRefreshSystem: () => void;
}) {
  async function changeSlm(profileId: string) {
    setSettings((current) => ({ ...current, slmProfileId: profileId }));
    if (!profileId) return;
    try {
      await client.selectSlmProfile(profileId);
      setNotice("Selected SLM profile updated on the backend.");
      onRefreshSystem();
    } catch (error) {
      setNotice(`Could not update backend SLM selection: ${cleanError(error)}`);
    }
  }

  return (
    <section className="settings-page">
      <PageTitle
        eyebrow="Settings"
        title="Local frontend settings"
        detail="These preferences are stored in localStorage. Defaults stay safe in preview/read-only mode."
      />
      {notice && <Notice tone="blue" text={notice} />}
      <div className="settings-grid">
        <label className="field">
          <span>Backend API URL</span>
          <input
            value={settings.apiUrl}
            onChange={(event) =>
              setSettings((current) => ({ ...current, apiUrl: event.target.value.trim() }))
            }
            placeholder="http://127.0.0.1:8000"
          />
        </label>
        <label className="field">
          <span>Selected SLM profile</span>
          <select
            value={settings.slmProfileId}
            onChange={(event) => void changeSlm(event.target.value)}
          >
            <option value="">Backend default</option>
            {(slmProfiles?.profiles ?? []).map((profile) => {
              const id = readString(profile.profile_id);
              return (
                <option key={id} value={id}>
                  {readString(profile.name, id)}
                </option>
              );
            })}
          </select>
        </label>
        <label className="field">
          <span>Safety mode</span>
          <select
            value={settings.safetyMode}
            onChange={(event) =>
              setSettings((current) => ({
                ...current,
                safetyMode: event.target.value as SafetyMode,
              }))
            }
          >
            <option value="read_only">Preview / read-only</option>
            <option value="confirm">Require confirmation</option>
          </select>
        </label>
        <ToggleField
          label="RAG enabled"
          checked={settings.ragEnabled}
          onChange={(value) => setSettings((current) => ({ ...current, ragEnabled: value }))}
        />
        <ToggleField
          label="Specialist routing enabled"
          checked={settings.specialistRoutingEnabled}
          onChange={(value) =>
            setSettings((current) => ({ ...current, specialistRoutingEnabled: value }))
          }
        />
      </div>
      <div className="settings-actions">
        <button className="secondary-button" onClick={onRefreshSystem}>
          <RefreshCw size={16} />
          Refresh backend data
        </button>
        <button className="danger-button" onClick={onReset}>
          <RotateCcw size={16} />
          Reset local state
        </button>
      </div>
    </section>
  );
}

function PageToolbar({
  eyebrow,
  title,
  detail,
  loading,
  onRefresh,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <div className="page-toolbar">
      <PageTitle eyebrow={eyebrow} title={title} detail={detail} />
      <button className="secondary-button" onClick={onRefresh} disabled={loading}>
        <RefreshCw size={16} className={loading ? "spin" : ""} />
        Refresh
      </button>
    </div>
  );
}

function PageTitle({
  eyebrow,
  title,
  detail,
}: {
  eyebrow: string;
  title: string;
  detail: string;
}) {
  return (
    <div className="page-title">
      <span>{eyebrow}</span>
      <h1>{title}</h1>
      <p>{detail}</p>
    </div>
  );
}

function PanelTitle({ icon: Icon, title }: { icon: typeof Bot; title: string }) {
  return (
    <div className="panel-title">
      <Icon size={17} />
      <h2>{title}</h2>
    </div>
  );
}

function StatusCard({
  icon: Icon,
  title,
  value,
  detail,
  tone = "neutral",
}: {
  icon: typeof Bot;
  title: string;
  value: string;
  detail: string;
  tone?: "neutral" | "green" | "amber" | "red";
}) {
  return (
    <section className={`status-card tone-${tone}`}>
      <Icon size={18} />
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </section>
  );
}

function StatusPill({
  label,
  tone,
}: {
  label: string;
  tone: "green" | "amber" | "red" | "blue";
}) {
  return <span className={`status-pill tone-${tone}`}>{label}</span>;
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "green" | "amber" | "red" | "blue";
}) {
  return (
    <span className={`metric ${tone ? `tone-${tone}` : ""}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function InfoList({ items }: { items: Array<[string, string]> }) {
  return (
    <dl className="info-list">
      {items.map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ToggleField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle-field">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

function Notice({ tone, text }: { tone: "amber" | "blue"; text: string }) {
  return (
    <div className={`notice ${tone}`}>
      <AlertTriangle size={16} />
      {text}
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  detail,
}: {
  icon: typeof Bot;
  title: string;
  detail: string;
}) {
  return (
    <div className="empty-state">
      <Icon size={24} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function EmptyInline({ text }: { text: string }) {
  return <div className="empty-inline">{text}</div>;
}

function TraceTimeline({ events }: { events: TraceEvent[] }) {
  if (!events.length) return <EmptyInline text="No trace timeline available for this run." />;
  return (
    <div className="trace-list">
      {events.map((event) => (
        <div key={event.id} className={`trace-row trace-${event.status}`}>
          <CheckCircle2 size={15} />
          <span>
            <strong>{event.title}</strong>
            <small>{event.phase} / {event.elapsed}</small>
            <em>{event.detail}</em>
          </span>
        </div>
      ))}
    </div>
  );
}

function buildChatRun({
  prompt,
  assistantText,
  route,
  validation,
  profile,
  response,
}: {
  prompt: string;
  assistantText: string;
  route: SpecialistRouteResult | null;
  validation: RuntimePlanValidation;
  profile: ExecutionProfile | null;
  response: SlmChatResponse;
}): ChatRun {
  const createdAt = new Date().toISOString();
  const id = newId("run");
  return {
    id,
    title: prompt.slice(0, 80) || "Chat run",
    createdAt,
    userMessage: prompt,
    assistantMessage: assistantText,
    selectedSpecialist: route?.recommended_specialist ?? "Not routed",
    runtimeProfile: profile ? `${profile.name} / ${profile.runtime}` : "No profile",
    safetyDecision: validation.decision,
    traceId: readString(response.trace_id) || null,
    validationReason: validation.reason,
    timeline: [
      traceEvent("accepted", "Task accepted", "User message submitted to live backend.", "passed", "0.0s"),
      traceEvent(
        "specialist",
        "Specialist routed",
        route
          ? `${route.recommended_specialist} at ${Math.round(route.confidence * 100)}% confidence.`
          : "Specialist routing disabled or unavailable.",
        route ? "passed" : "warning",
        "0.2s",
      ),
      traceEvent(
        "safety",
        `Plan ${validation.decision}`,
        validation.reason,
        validation.decision === "allow" ? "passed" : validation.decision === "block" ? "blocked" : "warning",
        "0.4s",
      ),
      traceEvent(
        "profile",
        "Runtime profile",
        profile ? `${profile.name} using ${profile.device}.` : "No runtime profile returned.",
        profile ? "passed" : "warning",
        "0.6s",
      ),
      traceEvent("response", "Assistant response", "Backend returned the assistant message.", "passed", "0.8s"),
    ],
  };
}

function traceEvent(
  phase: string,
  title: string,
  detail: string,
  status: TraceEvent["status"],
  elapsed: string,
): TraceEvent {
  return {
    id: `${phase}-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
    phase,
    title,
    detail,
    status,
    elapsed,
  };
}

function buildHistoryItems(runs: ChatRun[], data: HistoryData | null) {
  return [
    ...runs.map((run) => ({
      id: `chat:${run.id}`,
      title: run.title,
      kind: "Chat",
      meta: String(run.safetyDecision),
      createdAt: run.createdAt,
      decision: String(run.safetyDecision),
      detail: run.assistantMessage,
    })),
    ...(data?.jobs ?? []).map((job) => ({
      id: `job:${job.job_id}`,
      title: jobTitle(job),
      kind: "Job",
      meta: job.status,
      createdAt: job.finished_at ?? job.started_at ?? job.created_at,
      decision: readString(asObject(job.result).decision, "Unavailable"),
      detail: job.error ?? JSON.stringify(job.result ?? {}, null, 2),
    })),
    ...(data?.analyses ?? []).map((item) => ({
      id: `analysis:${item.analysis_id}`,
      title: item.filename ?? `Analysis ${item.analysis_id.slice(0, 8)}`,
      kind: "Analysis",
      meta: `${item.issue_count} issues`,
      createdAt: item.created_at,
      decision: item.issue_count === 0 ? "passed" : "review",
      detail: `${item.line_count} lines / ${item.phase}`,
    })),
    ...((data?.specialistTraces?.traces ?? []).slice(0, 10).map((trace, index) => ({
      id: `specialist:${readString(trace.trace_id, String(index))}`,
      title: readString(trace.recommended_specialist, "Specialist trace"),
      kind: "Specialist",
      meta: readString(trace.decision_source, "trace"),
      createdAt: readString(trace.timestamp, new Date().toISOString()),
      decision: readString(trace.recommended_specialist, "Unavailable"),
      detail: JSON.stringify(trace, null, 2),
    }))),
  ].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
}

function defaultPlanForTaskKind(taskKind: TaskKind): Record<string, unknown> {
  switch (taskKind) {
    case "Code repair":
      return { strategy: "code_repair", use_static_analysis: true };
    case "RAG workflow":
      return { strategy: "rag_retrieval", use_embeddings: true };
    case "Model training":
      return { strategy: "pytorch_training", model_size_billion_params: 1 };
    case "Classical ML":
      return { strategy: "sklearn_training", use_gpu: false };
    case "Local SLM":
    default:
      return { strategy: "local_inference", model_size_billion_params: 3 };
  }
}

function inferTaskKind(text: string): TaskKind {
  const lowered = text.toLowerCase();
  if (/(test|bug|fix|patch|repair|error|traceback)/.test(lowered)) return "Code repair";
  if (/(rag|retrieval|index|embedding)/.test(lowered)) return "RAG workflow";
  if (/(train|fine[- ]?tune|epoch|dataset)/.test(lowered)) return "Model training";
  if (/(sklearn|classifier|regression|tabular)/.test(lowered)) return "Classical ML";
  return "Local SLM";
}

function looksDestructive(text: string) {
  return /(delete|remove|overwrite|apply patch|write file|commit|push|deploy|drop|truncate|rollback)/i.test(text);
}

async function settle<T>(promise: Promise<T>): Promise<{ value: T | null; error: string | null }> {
  try {
    return { value: await promise, error: null };
  } catch (error) {
    return { value: null, error: cleanError(error) };
  }
}

function loadSettings(): FrontendSettings {
  try {
    const parsed = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}");
    return {
      ...defaultSettings,
      ...asObject(parsed),
      safetyMode: parsed.safetyMode === "confirm" ? "confirm" : "read_only",
      ragEnabled: parsed.ragEnabled !== false,
      specialistRoutingEnabled: parsed.specialistRoutingEnabled !== false,
    };
  } catch {
    return defaultSettings;
  }
}

function loadRuns(): ChatRun[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(CHAT_HISTORY_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function labelSafety(mode: SafetyMode) {
  return mode === "confirm" ? "Confirm before action" : "Preview / read-only";
}

function decisionTone(decision: PlanDecision | "unknown") {
  if (decision === "allow") return "green";
  if (decision === "downgrade") return "amber";
  if (decision === "block") return "red";
  return "blue";
}

function jobTitle(job: RawJob) {
  const result = asObject(job.result);
  return readString(result.goal) || job.job_type.replace(/_/g, " ");
}

function formatAge(iso: string) {
  const timestamp = new Date(iso).getTime();
  if (!Number.isFinite(timestamp)) return "unknown";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatDate(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}

function cleanError(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function readString(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function newId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default App;
