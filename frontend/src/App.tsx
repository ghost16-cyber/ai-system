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
  type ChatTraceEntry,
  type ChatRunResponse,
  type HealthData,
  type RagStatusResponse,
  type RawHistoryItem,
  type RawJob,
  type RawTool,
  type SelectedSlmResponse,
  type SlmProfilesResponse,
  type SlmStatusResponse,
} from "./clients/astraClient";
import type {
  CompactTraceResponse,
  RuntimeContext,
  SpecialistDashboard,
  SpecialistModelsResponse,
  SpecialistTracesResponse,
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
  meta?: ChatRunResponse;
}

interface SystemData {
  health: HealthData | null;
  runtime: RuntimeContext | null;
  selectedSlm: SelectedSlmResponse | null;
  slmProfiles: SlmProfilesResponse | null;
  slmStatus: SlmStatusResponse | null;
  rag: RagStatusResponse | null;
  specialistDashboard: SpecialistDashboard | null;
  specialistModels: SpecialistModelsResponse | null;
  tools: RawTool[];
}

interface HistoryData {
  chatRuns: ChatRunResponse[];
  jobs: RawJob[];
  analyses: RawHistoryItem[];
  specialistTraces: SpecialistTracesResponse | null;
}

interface HistoryItem {
  id: string;
  title: string;
  kind: string;
  meta: string;
  preview?: string;
  createdAt: string;
  decision?: string;
  detail?: string;
}

const SETTINGS_KEY = "astra.phase49.settings";

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
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
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

  const refreshSystem = useCallback(async () => {
    setSystemLoading(true);
    setSystemError(null);
    const [
      health,
      runtime,
      selectedSlm,
      slmProfiles,
      slmStatus,
      rag,
      specialistDashboard,
      specialistModels,
      tools,
    ] = await Promise.all([
      settle(client.getHealth()),
      settle(client.getRuntimeContext()),
      settle(client.getSelectedSlm()),
      settle(client.getSlmProfiles()),
      settle(client.getSlmStatus()),
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
      slmStatus: slmStatus.value,
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
      slmStatus.error,
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
    const [chatRuns, jobs, analyses, traces] = await Promise.all([
      settle(client.getChatRuns(30)),
      settle(client.getJobs(30)),
      settle(client.getHistory(30)),
      settle(client.getSpecialistTraces()),
    ]);
    setHistoryData({
      chatRuns: chatRuns.value ?? [],
      jobs: jobs.value ?? [],
      analyses: analyses.value ?? [],
      specialistTraces: traces.value,
    });
    setHistoryError(chatRuns.error ?? jobs.error ?? analyses.error ?? traces.error ?? null);
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
      const run = await client.runChat({
        message: prompt,
        use_rag: settings.ragEnabled,
        safety_mode: settings.safetyMode,
        conversation_id: activeConversationId,
      });
      setActiveConversationId(run.conversation_id);
      const assistantText =
        readString(run.assistant_response) ||
        "Astra completed the request, but the backend did not return response text.";
      const assistantMessage: ChatMessage = {
        id: newId("assistant"),
        role: "assistant",
        text: assistantText,
        createdAt: run.created_at,
        meta: run,
      };
      setMessages((current) => [...current, assistantMessage]);
      setSelectedRunId(`chat:${run.run_id}`);
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

  function startNewChat() {
    setActiveConversationId(null);
    setMessages([]);
    setChatError(null);
    setLastPrompt(null);
    setSelectedRunId(null);
  }

  function resetLocalState() {
    localStorage.removeItem(SETTINGS_KEY);
    setMessages([]);
    setSelectedRunId(null);
    setSettings(defaultSettings);
    setSettingsNotice("Frontend settings and the visible chat transcript were reset.");
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
            onNewChat={startNewChat}
            activeConversationId={activeConversationId}
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
  onNewChat,
  activeConversationId,
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
  onNewChat: () => void;
  activeConversationId: string | null;
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
        <div className="page-toolbar">
          <PageTitle
            eyebrow="Chat"
            title="Ask Astra"
            detail="Live backend calls only. Chat runs in preview mode and does not apply patches, delete files, or execute destructive actions."
          />
          <button className="secondary-button" onClick={onNewChat} disabled={loading}>
            <MessageSquareText size={16} />
            New chat
          </button>
        </div>
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
            ["Conversation", activeConversationId ? activeConversationId.slice(0, 8) : "Fresh chat"],
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

function ChatResultMeta({ run }: { run: ChatRunResponse }) {
  return (
    <div className="result-meta">
      <Metric label="Specialist" value={run.selected_specialist || "Not routed"} />
      <Metric label="Intent" value={`${run.intent || "unknown"} / ${Math.round((run.confidence ?? 0) * 100)}%`} />
      <Metric
        label="RAG"
        value={ragDisplay(run)}
        tone={run.rag_used ? "green" : "blue"}
      />
      <Metric label="Safety" value={run.safety_decision || "unknown"} tone={decisionTone(run.safety_decision)} />
      <Metric label="Runtime" value={run.runtime_decision || "unknown"} />
      <Metric
        label="SLM"
        value={run.used_real_slm ? `${run.slm_provider} / ${run.slm_model ?? "selected model"}` : `Fallback / ${run.slm_model ?? "no model"}`}
        tone={run.used_real_slm ? "green" : "amber"}
      />
      <Metric
        label={run.used_real_slm ? "SLM latency" : "Fallback reason"}
        value={run.used_real_slm ? `${run.slm_latency_ms ?? 0} ms` : run.slm_fallback_reason ?? "Unavailable"}
        tone={run.used_real_slm ? "green" : "amber"}
      />
      <Metric
        label="Memory"
        value={run.memory_used ? "used" : "not used"}
        tone={run.memory_used ? "green" : "blue"}
      />
      <Metric label="Run ID" value={run.run_id ? run.run_id.slice(0, 8) : "Not returned"} />
      <div className="meta-reason">
        <TraceTimeline events={traceSummaryToEvents(run.trace_summary ?? [])} />
      </div>
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
              ["Ollama route", data?.slmStatus ? (data.slmStatus.reachable ? "Reachable" : "Unreachable") : "Unavailable"],
              ["Gateway model", data?.slmStatus?.configured_model || data?.slmStatus?.selected_model || readString(selectedProfile.model_name) || "Unavailable"],
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
  data,
  loading,
  error,
  selectedRunId,
  setSelectedRunId,
  selectedTrace,
  onRefresh,
}: {
  data: HistoryData | null;
  loading: boolean;
  error: string | null;
  selectedRunId: string | null;
  setSelectedRunId: (id: string) => void;
  selectedTrace: CompactTraceResponse | null;
  onRefresh: () => void;
}) {
  const items = buildHistoryItems(data);
  const selected = items.find((item) => item.id === selectedRunId) ?? items[0] ?? null;
  const selectedConversationRuns =
    selected?.kind === "Conversation"
      ? (data?.chatRuns ?? [])
          .filter((run) => `conversation:${run.conversation_id}` === selected.id)
          .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      : [];
  const selectedChatRun =
    selectedConversationRuns.length > 0
      ? selectedConversationRuns[selectedConversationRuns.length - 1]
      : null;

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
                {item.preview && <span>{item.preview}</span>}
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
                    ...(selectedChatRun
                      ? [
                          ["Conversation", selectedChatRun.conversation_id] as [string, string],
                          ["Turns", String(selectedConversationRuns.length)] as [string, string],
                          ["Specialist", selectedChatRun.selected_specialist || "Not routed"] as [string, string],
                          ["Intent", `${selectedChatRun.intent || "unknown"} / ${Math.round((selectedChatRun.confidence ?? 0) * 100)}%`] as [string, string],
                          ["RAG", ragDisplay(selectedChatRun)] as [string, string],
                          ["Memory", selectedChatRun.memory_used ? "used" : "not used"] as [string, string],
                          ["SLM", selectedChatRun.used_real_slm ? `${selectedChatRun.slm_provider} / ${selectedChatRun.slm_model ?? "selected model"}` : `Fallback / ${selectedChatRun.slm_model ?? "no model"}`] as [string, string],
                          ["SLM detail", selectedChatRun.used_real_slm ? `${selectedChatRun.slm_latency_ms ?? 0} ms` : selectedChatRun.slm_fallback_reason ?? "Unavailable"] as [string, string],
                        ]
                      : []),
                  ]}
                />
                {selectedConversationRuns.length > 0 && (
                  <>
                    <h3>Conversation turns</h3>
                    <div className="turn-list">
                      {selectedConversationRuns.map((turn, index) => (
                        <details key={turn.run_id} className="turn-detail">
                          <summary>
                            <strong>Turn {index + 1}</strong>
                            <span>{turn.user_message.slice(0, 90)}</span>
                            <small>{formatAge(turn.created_at)}</small>
                          </summary>
                          <InfoList
                            items={[
                              ["Specialist", turn.selected_specialist || "Not routed"],
                              ["RAG", ragDisplay(turn)],
                              ["Memory", turn.memory_used ? "used" : "not used"],
                              ["Safety/runtime", `${turn.safety_decision || "unknown"} / ${turn.runtime_decision || "unknown"}`],
                            ]}
                          />
                          <p>{turn.assistant_response}</p>
                          <details>
                            <summary>Raw trace details</summary>
                            <pre className="json-preview">
                              {JSON.stringify(
                                {
                                  ...turn,
                                  trace_summary: turn.trace_summary,
                                },
                                null,
                                2,
                              )}
                            </pre>
                          </details>
                        </details>
                      ))}
                    </div>
                  </>
                )}
                <h3>Trace timeline</h3>
                <TraceTimeline
                  events={
                    selected.kind === "Conversation"
                      ? traceSummaryToEvents(selectedChatRun?.trace_summary ?? [])
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

function buildHistoryItems(data: HistoryData | null): HistoryItem[] {
  const conversationItems = Array.from(groupChatRuns(data?.chatRuns ?? []).entries()).map(
    ([conversationId, turns]) => {
      const sorted = [...turns].sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
      const first = sorted[0];
      const latest = sorted[sorted.length - 1];
      return {
        id: `conversation:${conversationId}`,
        title: first?.user_message.slice(0, 80) || "Chat conversation",
        kind: "Conversation",
        meta: `${sorted.length} turn${sorted.length === 1 ? "" : "s"}`,
        preview: latest?.assistant_response.slice(0, 140),
        createdAt: latest?.created_at ?? new Date().toISOString(),
        decision: latest
          ? `${latest.safety_decision || "unknown"} / ${latest.runtime_decision || "unknown"} / RAG ${latest.rag_used ? "used" : "not used"}`
          : "Unavailable",
        detail: JSON.stringify(
          {
            conversation_id: conversationId,
            title: first?.user_message,
            turn_count: sorted.length,
            latest_timestamp: latest?.created_at,
            latest_specialist: latest?.selected_specialist,
            latest_rag_used: latest?.rag_used,
            latest_rag_skip_reason: latest?.rag_skip_reason,
            latest_safety_decision: latest?.safety_decision,
            latest_runtime_decision: latest?.runtime_decision,
            memory_summary: latest?.memory_summary,
            turns: sorted.map((run) => ({
              run_id: run.run_id,
              user_message: run.user_message,
              assistant_response: run.assistant_response,
              selected_specialist: run.selected_specialist,
              intent: run.intent,
              confidence: run.confidence,
              rag_used: run.rag_used,
              rag_skip_reason: run.rag_skip_reason,
              rag_context_count: run.rag_context_count,
              memory_used: run.memory_used,
              used_real_slm: run.used_real_slm,
              slm_provider: run.slm_provider,
              slm_model: run.slm_model,
              slm_fallback_reason: run.slm_fallback_reason,
              slm_latency_ms: run.slm_latency_ms,
              safety_decision: run.safety_decision,
              runtime_decision: run.runtime_decision,
              created_at: run.created_at,
            })),
          },
          null,
          2,
        ),
      };
    },
  );

  return [
    ...conversationItems,
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

function groupChatRuns(runs: ChatRunResponse[]) {
  const groups = new Map<string, ChatRunResponse[]>();
  for (const run of runs) {
    const conversationId = run.conversation_id || run.run_id;
    groups.set(conversationId, [...(groups.get(conversationId) ?? []), run]);
  }
  return groups;
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

function labelSafety(mode: SafetyMode) {
  return mode === "confirm" ? "Confirm before action" : "Preview / read-only";
}

function ragDisplay(run: ChatRunResponse) {
  if (run.rag_used) return `Used (${run.rag_context_count ?? 0})`;
  const directReason = readString(run.rag_skip_reason);
  if (directReason === "greeting") return "Skipped: greeting";
  if (directReason === "system_meta_question") return "Skipped: system/meta question";
  if (directReason === "low_relevance") return "Skipped: low relevance";
  if (directReason === "disabled") return "Skipped: disabled";
  const ragTrace = run.trace_summary?.find((entry) => entry.phase === "rag");
  const reason = readString(ragTrace?.data?.reason);
  if (reason === "greeting") return "Skipped: greeting";
  if (reason === "system_meta_question") return "Skipped: system/meta question";
  if (reason === "low_relevance") return "Skipped: low relevance";
  if (reason === "disabled") return "Skipped: disabled";
  return "Skipped";
}

function traceSummaryToEvents(entries: ChatTraceEntry[]): TraceEvent[] {
  return entries.map((entry, index) => {
    const status = ["passed", "active", "warning", "blocked"].includes(entry.status)
      ? (entry.status as TraceEvent["status"])
      : "warning";
    return {
      id: `${entry.phase || "trace"}-${index}`,
      phase: entry.phase || "trace",
      title: entry.title || "Trace event",
      detail: entry.detail || "No detail returned.",
      status,
      elapsed: `${index + 1}`,
    };
  });
}

function decisionTone(decision: string | null | undefined) {
  const normalized = (decision ?? "").toLowerCase();
  if (normalized === "allow" || normalized === "allowed") return "green";
  if (normalized === "downgrade" || normalized === "downgraded" || normalized === "read_only") return "amber";
  if (normalized === "block" || normalized === "blocked") return "red";
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
