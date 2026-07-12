import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Database,
  History,
  Plus,
  Send,
  Server,
  ShieldCheck,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  HttpAstraClient,
  type AssignmentCopilotResult,
  type ChatRunResponse,
  type HealthData,
} from "./clients/astraClient";
import {
  actionFromPayload,
  type ChatAction,
  type ChatActionStatus,
} from "./state/chatActionState";
import {
  approveAndExecuteCommand,
  commandResultPresentation,
  tryLockCommandAction,
} from "./state/chatCommandState";

interface Settings {
  apiUrl: string;
  ragEnabled: boolean;
  safetyMode: string;
}

interface InfoCard {
  icon: "server" | "history" | "database" | "settings" | "assignment";
  title: string;
  summary: string;
  rows: Array<{ label: string; value: string; conversationId?: string }>;
  technical?: unknown;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  createdAt: string;
  run?: ChatRunResponse;
  action?: ChatAction;
  info?: InfoCard;
}

const SETTINGS_KEY = "astra.chat.settings";
const defaultSettings: Settings = {
  apiUrl: "http://127.0.0.1:8000",
  ragEnabled: true,
  safetyMode: "confirm",
};

export default function App() {
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [awaitingAssignment, setAwaitingAssignment] = useState(false);
  const [assignmentResult, setAssignmentResult] = useState<AssignmentCopilotResult | null>(null);
  const locks = useRef(new Set<string>());
  const client = useMemo(() => new HttpAstraClient(settings.apiUrl), [settings.apiUrl]);

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    let active = true;
    const refresh = () => client.getHealth().then((value) => active && setHealth(value)).catch(() => active && setHealth(null));
    void refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [client]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const prompt = input.trim();
    if (!prompt || loading) return;
    setInput("");
    setError(null);
    setMessages((current) => [...current, makeMessage("user", prompt)]);
    setLoading(true);
    try {
      if (await handleNativeRequest(prompt)) return;
      await runOrdinaryChat(prompt);
    } catch (caught) {
      const message = cleanError(caught);
      setError(message);
      setMessages((current) => [...current, makeMessage("assistant", `I could not complete that request: ${message}`)]);
    } finally {
      setLoading(false);
    }
  }

  async function runOrdinaryChat(prompt: string) {
    let streamed = "";
    const assistantId = newId("assistant");
    setMessages((current) => [...current, { ...makeMessage("assistant", ""), id: assistantId }]);
    const run = await client.streamChat({
      message: prompt,
      use_rag: settings.ragEnabled,
      safety_mode: settings.safetyMode,
      conversation_id: conversationId,
    }, (event) => {
      if (event.event !== "response_delta") return;
      const delta = typeof event.data.delta === "string" ? event.data.delta : "";
      streamed += delta;
      setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, text: streamed } : item));
    });
    setConversationId(run.conversation_id);
    const action = run.action ? actionFromPayload(run.action) : null;
    setMessages((current) => current.map((item) => item.id === assistantId ? {
      ...item,
      text: action ? "" : run.assistant_response,
      createdAt: run.created_at,
      run,
      action: action ?? undefined,
    } : item));
  }

  async function handleNativeRequest(prompt: string): Promise<boolean> {
    const normalized = normalize(prompt);
    if (awaitingAssignment) {
      setAwaitingAssignment(false);
      const looksLikePath = /\.(?:txt|md|docx)$/i.test(prompt) && !prompt.includes("\n");
      const result = await client.runAssignmentCopilot(looksLikePath
        ? { path: prompt, selected_assignment: "all" }
        : { text: prompt, selected_assignment: "all" });
      setAssignmentResult(result);
      addInfo({
        icon: "assignment",
        title: "Assignment analysis",
        summary: result.next_recommended_step,
        rows: [
          { label: "Sections found", value: String(result.extracted_assignment_sections.length) },
          { label: "Tools executed", value: result.tools_executed ? "Yes" : "No" },
          { label: "Files written", value: result.files_written ? "Yes" : "No" },
        ],
        technical: result,
      });
      return true;
    }
    if (normalized === "read this assignment" || normalized === "read an assignment") {
      setAwaitingAssignment(true);
      setMessages((current) => [...current, makeMessage("assistant", "Paste the assignment text or send a local .txt, .md, or .docx path in your next message.")]);
      return true;
    }
    if (normalized === "check assignment readiness") {
      if (!assignmentResult) {
        setMessages((current) => [...current, makeMessage("assistant", "Read an assignment in this chat first, then ask me to check its readiness.")]);
      } else {
        addInfo({ icon: "assignment", title: "Assignment readiness", summary: assignmentResult.next_recommended_step, rows: assignmentResult.marking_readiness.slice(0, 8).map((item, index) => ({ label: String(item.assignment_name ?? `Item ${index + 1}`), value: String(item.status ?? item.readiness ?? "Review required") })), technical: assignmentResult.marking_readiness });
      }
      return true;
    }
    if (["show system status", "system status"].includes(normalized)) {
      const [currentHealth, model, rag] = await Promise.all([client.getHealth(), client.getSelectedSlm(), client.getRagStatus()]);
      setHealth(currentHealth);
      addInfo({ icon: "server", title: "System status", summary: `${currentHealth.service} is ${currentHealth.status}.`, rows: [{ label: "Backend", value: currentHealth.status }, { label: "Database", value: currentHealth.database }, { label: "Model", value: model.selected_profile_id }, { label: "RAG", value: rag.status }], technical: { health: currentHealth, model, rag } });
      return true;
    }
    if (["show my history", "show recent chats", "show my recent chats", "show history"].includes(normalized)) {
      const runs = await client.getChatRuns(8);
      addInfo({ icon: "history", title: "Recent chats", summary: runs.length ? `${runs.length} recent messages are available. Select one to continue it here.` : "No saved chats yet.", rows: runs.map((run) => ({ label: formatTime(run.created_at), value: run.user_message, conversationId: run.conversation_id })), technical: runs });
      return true;
    }
    if (["what model are you using", "what model are you using?", "show model", "show runtime"].includes(normalized)) {
      const model = await client.getSelectedSlm();
      addInfo({ icon: "server", title: "Selected model", summary: model.selected_profile_id, rows: [{ label: "Loaded", value: model.loaded ? "Yes" : "No" }, { label: "Profile", value: model.selected_profile_id }], technical: model });
      return true;
    }
    if (["show rag status", "rag status"].includes(normalized)) {
      const rag = await client.getRagStatus();
      addInfo({ icon: "database", title: "RAG status", summary: `Project retrieval is ${settings.ragEnabled ? "enabled" : "disabled"} for chat.`, rows: [{ label: "Index", value: rag.status }, { label: "Indexed files", value: String(rag.project_index_file_count ?? rag.indexed_file_count) }, { label: "Indexed chunks", value: String(rag.project_index_chunk_count ?? 0) }], technical: rag });
      return true;
    }
    if (["show settings", "show safety mode"].includes(normalized)) {
      addInfo({ icon: "settings", title: "Chat settings", summary: "Astra can execute allowlisted actions only after explicit approval.", rows: [{ label: "RAG", value: settings.ragEnabled ? "Enabled" : "Disabled" }, { label: "Safety", value: "Approval required" }, { label: "API", value: settings.apiUrl }], technical: settings });
      return true;
    }
    if (normalized === "enable rag" || normalized === "disable rag") {
      const enabled = normalized === "enable rag";
      setSettings((current) => ({ ...current, ragEnabled: enabled }));
      setMessages((current) => [...current, makeMessage("assistant", `RAG is now ${enabled ? "enabled" : "disabled"} for future chat responses.`)]);
      return true;
    }
    if (["change the selected model", "change model"].includes(normalized)) {
      const profiles = await client.getSlmProfiles();
      const options = profiles.profiles.map((profile) => {
        const id = String(profile.profile_id ?? profile.id ?? profile.name ?? "");
        return { id, label: String(profile.display_name ?? profile.name ?? id) };
      }).filter((option) => option.id);
      setMessages((current) => [...current, {
        ...makeMessage("assistant", ""),
        action: {
          actionType: "system_configuration",
          title: "Change the selected model",
          summary: "Choose a model profile, then explicitly approve the configuration change.",
          steps: ["Choose a profile", "Approve the change", "Confirm the active model"],
          safetyInformation: { approval_required: true },
          status: "awaiting_approval",
          approvalRequired: true,
          technicalDetails: { available_profile_count: options.length },
          options,
          selectedOption: options[0]?.id,
        },
      }]);
      return true;
    }
    return false;
  }

  function addInfo(info: InfoCard) {
    setMessages((current) => [...current, { ...makeMessage("assistant", ""), info }]);
  }

  function updateAction(messageId: string, change: (action: ChatAction) => ChatAction) {
    setMessages((current) => current.map((item) => item.id === messageId && item.action ? { ...item, action: change(item.action) } : item));
  }

  async function approveAction(messageId: string, action: ChatAction) {
    if (action.status !== "awaiting_approval") return;
    if (action.actionType === "system_configuration") {
      const lockId = `model:${action.selectedOption ?? ""}`;
      if (!action.selectedOption || !tryLockCommandAction(locks.current, lockId)) return;
      try {
        updateAction(messageId, (current) => ({ ...current, status: "approving", error: undefined }));
        await client.selectSlmProfile(action.selectedOption);
        updateAction(messageId, (current) => ({ ...current, status: "completed", resultSummary: `Selected model changed to ${action.selectedOption}.` }));
      } catch (caught) {
        updateAction(messageId, (current) => ({ ...current, status: "failed", error: cleanError(caught) }));
      } finally { locks.current.delete(lockId); }
      return;
    }
    const plan = action.commandPlan;
    if (!plan || !tryLockCommandAction(locks.current, plan.plan_id)) return;
    try {
      updateAction(messageId, (current) => ({ ...current, status: "approving", error: undefined }));
      const result = await approveAndExecuteCommand({
        calls: {
          approve: (id, request) => client.approveAssignmentCommand(id, request),
          execute: (id, request) => client.executeAssignmentCommand(id, request),
        },
        planId: plan.plan_id,
        association: { assignment_id: plan.assignment_id, workspace_path: plan.workspace || "." },
        onApproved: (approved) => updateAction(messageId, (current) => ({ ...current, status: "approved", commandPlan: approved })),
        beforeExecution: nextPaint,
        onRunning: () => updateAction(messageId, (current) => ({ ...current, status: "running" })),
      });
      const presentation = commandResultPresentation(result);
      updateAction(messageId, (current) => ({ ...current, status: result.exit_code === 0 ? "completed" : "failed", commandPlan: result, resultSummary: presentation.summary, error: presentation.errorTail || undefined }));
    } catch (caught) {
      updateAction(messageId, (current) => ({ ...current, status: "failed", error: cleanError(caught) }));
    } finally { locks.current.delete(plan.plan_id); }
  }

  async function cancelAction(messageId: string, action: ChatAction) {
    if (action.status !== "awaiting_approval") return;
    if (action.actionType === "system_configuration") {
      updateAction(messageId, (current) => ({ ...current, status: "cancelled", resultSummary: "Action cancelled. No setting was changed." }));
      return;
    }
    const plan = action.commandPlan;
    if (!plan || !tryLockCommandAction(locks.current, plan.plan_id)) return;
    try {
      const cancelled = await client.cancelAssignmentCommand(plan.plan_id, { assignment_id: plan.assignment_id, workspace_path: plan.workspace || "." });
      updateAction(messageId, (current) => ({ ...current, status: "cancelled", commandPlan: cancelled, resultSummary: "Action cancelled. No command was executed." }));
    } catch (caught) {
      updateAction(messageId, (current) => ({ ...current, error: cleanError(caught) }));
    } finally { locks.current.delete(plan.plan_id); }
  }

  function newChat() {
    setConversationId(null);
    setMessages([]);
    setError(null);
    setAwaitingAssignment(false);
  }

  async function continueConversation(selectedConversationId: string) {
    setLoading(true);
    try {
      const runs = (await client.getChatRuns(100))
        .filter((run) => run.conversation_id === selectedConversationId)
        .reverse();
      const restored = runs.flatMap<Message>((run) => [
        { ...makeMessage("user", run.user_message), createdAt: run.created_at },
        {
          ...makeMessage("assistant", run.action ? "" : run.assistant_response),
          createdAt: run.created_at,
          run,
          action: run.action ? actionFromPayload(run.action) ?? undefined : undefined,
        },
      ]);
      setConversationId(selectedConversationId);
      setMessages(restored);
      setError(null);
    } catch (caught) {
      setError(cleanError(caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand"><span className="brand-mark"><Bot size={22} /></span><div><strong>Astra</strong><span>Local AI assistant</span></div></div>
        <div className="header-actions">
          <span className={`connection ${health ? "online" : "offline"}`}><span />{health ? "Backend connected" : "Backend unavailable"}</span>
          <button className="secondary-button" onClick={newChat}><Plus size={16} />New chat</button>
        </div>
      </header>
      <main className="chat-shell">
        <section className="conversation" aria-label="Conversation">
          {messages.length === 0 && <Welcome onPrompt={(prompt) => { setInput(prompt); }} />}
          {messages.map((message) => <ChatMessage key={message.id} message={message} onApprove={approveAction} onCancel={cancelAction} onOption={(option) => updateAction(message.id, (action) => ({ ...action, selectedOption: option }))} onContinue={continueConversation} />)}
          {loading && <div className="message assistant"><Avatar role="assistant" /><div className="bubble loading"><Activity className="spin" size={17} />Astra is working…</div></div>}
        </section>
        <form className="composer" onSubmit={submit}>
          {error && <div className="composer-error"><CircleAlert size={15} />{error}</div>}
          <div className="composer-box"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="Message Astra…" rows={1} disabled={loading} /><button className="send-button" disabled={!input.trim() || loading} aria-label="Send"><Send size={18} /></button></div>
          <p>Approval required for execution. Unsupported and destructive actions remain blocked.</p>
        </form>
      </main>
    </div>
  );
}

function ChatMessage({ message, onApprove, onCancel, onOption, onContinue }: { message: Message; onApprove: (id: string, action: ChatAction) => Promise<void>; onCancel: (id: string, action: ChatAction) => Promise<void>; onOption: (option: string) => void; onContinue: (conversationId: string) => Promise<void> }) {
  return <article className={`message ${message.role}`}><Avatar role={message.role} /><div className="bubble">
    {message.text && <p className="message-text">{message.text}</p>}
    {message.action && <ActionCard action={message.action} onApprove={() => void onApprove(message.id, message.action!)} onCancel={() => void onCancel(message.id, message.action!)} onOption={onOption} />}
    {message.info && <InfoCardView card={message.info} onContinue={onContinue} />}
    {message.run && !message.action && <RunDetails run={message.run} />}
  </div></article>;
}

function ActionCard({ action, onApprove, onCancel, onOption }: { action: ChatAction; onApprove: () => void; onCancel: () => void; onOption: (option: string) => void }) {
  const plan = action.commandPlan;
  const busy = ["approving", "approved", "running"].includes(action.status);
  return <div className="action-card">
    <div className="card-heading"><div><span className="eyebrow">{action.actionType.replace(/_/g, " ")}</span><h2>{action.title}</h2></div><Status status={action.status} /></div>
    <p>{action.summary}</p>
    {plan && <div className="command-preview"><span>Command</span><code>{plan.command}</code></div>}
    {plan && <p className="muted">Working directory: <span className="friendly-location">Project workspace</span></p>}
    {action.options && <label className="model-choice">Model profile<select value={action.selectedOption} onChange={(event) => onOption(event.target.value)} disabled={busy}>{action.options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>}
    {action.status === "awaiting_approval" && <div className="button-row"><button className="primary-button" onClick={onApprove}><ShieldCheck size={16} />{action.actionType === "command" ? "Approve and run" : "Approve change"}</button><button className="secondary-button danger" onClick={onCancel}><X size={16} />Cancel</button></div>}
    {busy && <div className="progress-line"><Activity className={action.status === "approved" ? "" : "spin"} size={16} />{statusText(action.status)}</div>}
    {action.resultSummary && <div className={`result ${action.status}`}><CheckCircle2 size={17} />{action.resultSummary}</div>}
    {action.error && <div className="result failed"><CircleAlert size={17} /><div><strong>Relevant error</strong><pre>{action.error}</pre></div></div>}
    {plan && <details className="technical workspace-technical"><summary><ChevronDown size={15} />Workspace details</summary><div className="technical-body"><span>Resolved working directory: <code>{plan.workspace || "."}</code></span></div></details>}
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body">{plan && <><span>Exit code: {plan.exit_code ?? "—"}</span><span>Timeout: {plan.timeout_seconds}s</span><span>Risk: {plan.risk_level}</span>{["completed", "failed"].includes(action.status) && <><label>Full redacted stdout<pre>{plan.stdout || "(empty)"}</pre></label><label>Full redacted stderr<pre>{plan.stderr || "(empty)"}</pre></label></>}</>}<JsonBlock value={{ steps: action.steps, safety: action.safetyInformation, details: withoutPlan(action.technicalDetails) }} /></div></details>
  </div>;
}

function InfoCardView({ card, onContinue }: { card: InfoCard; onContinue: (conversationId: string) => Promise<void> }) {
  const Icon = card.icon === "history" ? History : card.icon === "database" ? Database : card.icon === "settings" ? ShieldCheck : Server;
  return <div className="info-card"><div className="card-heading"><div className="info-title"><Icon size={18} /><h2>{card.title}</h2></div></div><p>{card.summary}</p><dl>{card.rows.map((row, index) => <div key={`${row.label}-${index}`}><dt>{row.label}</dt><dd>{row.conversationId ? <button className="history-link" onClick={() => void onContinue(row.conversationId!)}>{row.value}</button> : row.value}</dd></div>)}</dl>{card.technical !== undefined && <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><JsonBlock value={card.technical} /></details>}</div>;
}

function RunDetails({ run }: { run: ChatRunResponse }) {
  return <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-grid"><span>Model: {run.slm_model ?? run.slm_provider}</span><span>Specialist: {run.selected_specialist}</span><span>RAG: {run.rag_used ? `${run.rag_context_count} sources` : "not used"}</span><span>Safety: {run.safety_decision}</span><span>Latency: {run.slm_latency_ms === null ? "—" : `${run.slm_latency_ms}ms`}</span><span>Run: {run.run_id}</span></div></details>;
}

function Welcome({ onPrompt }: { onPrompt: (prompt: string) => void }) {
  return <div className="welcome"><span className="welcome-icon"><Bot size={30} /></span><h1>What can I help with?</h1><p>Ask a question, inspect Astra, or request an allowlisted action directly in chat.</p><div className="suggestions">{["Run the tests", "Show system status", "Show recent chats", "What model are you using?"].map((prompt) => <button key={prompt} onClick={() => onPrompt(prompt)}>{prompt}</button>)}</div></div>;
}

function Avatar({ role }: { role: "user" | "assistant" }) { return <div className="avatar">{role === "user" ? "You" : <Bot size={17} />}</div>; }
function Status({ status }: { status: ChatActionStatus }) { return <span className={`status status-${status}`}>{status.replace(/_/g, " ")}</span>; }
function JsonBlock({ value }: { value: unknown }) { return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>; }
function withoutPlan(details: Record<string, unknown>) { const rest = { ...details }; delete rest.command_plan; return rest; }
function statusText(status: ChatActionStatus) { return status === "approving" ? "Recording approval…" : status === "approved" ? "Approved. Starting…" : "Running the approved action…"; }
function makeMessage(role: Message["role"], text: string): Message { return { id: newId(role), role, text, createdAt: new Date().toISOString() }; }
function newId(prefix: string) { return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
function normalize(value: string) { return value.trim().toLowerCase().replace(/[.!?]+$/, "").replace(/\s+/g, " "); }
function cleanError(error: unknown) { return error instanceof Error ? error.message : String(error); }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Saved chat" : date.toLocaleString(); }
function nextPaint() { return new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); }
function loadSettings(): Settings { try { const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null") as Partial<Settings> | null; return { ...defaultSettings, ...(stored ?? {}) }; } catch { return defaultSettings; } }
