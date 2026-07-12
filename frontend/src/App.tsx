import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Database,
  FileText,
  FolderOpen,
  History,
  Paperclip,
  Plus,
  RefreshCw,
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
import {
  assignmentAnalysisFromActionPayload,
  assignmentWorkspaceActionFromPayload,
  isAssignmentWorkspaceRequest,
  type AssignmentWorkspaceAction,
} from "./state/assignmentWorkspaceState";
import {
  folderAccessActionFromPayload,
  type FolderAccessAction,
} from "./state/folderAccessState";

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
  workspaceAction?: AssignmentWorkspaceAction;
  folderAction?: FolderAccessAction;
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
  const [selectedAssignmentFile, setSelectedAssignmentFile] = useState<File | null>(null);
  const assignmentFileInputRef = useRef<HTMLInputElement | null>(null);
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
    const attachedFile = selectedAssignmentFile;
    if ((!prompt && !attachedFile) || loading) return;
    const submittedText = prompt || `Read assignment: ${attachedFile?.name ?? "attached file"}`;
    setInput("");
    setError(null);
    setMessages((current) => [...current, makeMessage("user", submittedText)]);
    setLoading(true);
    try {
      if (attachedFile) {
        const uploaded = await client.uploadAssignment(attachedFile);
        const run = await client.createChatAssignmentAnalysis({
          path: uploaded.path,
          selected_assignment: "all",
          conversation_id: conversationId,
          user_message: submittedText,
        });
        showAssignmentAnalysisRun(run);
        clearAssignmentFile();
        setAwaitingAssignment(false);
        return;
      }
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

  function clearAssignmentFile() {
    setSelectedAssignmentFile(null);
    if (assignmentFileInputRef.current) assignmentFileInputRef.current.value = "";
  }

  function selectAssignmentFile(file: File | null) {
    if (!file) return;
    const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? "";
    if (![".txt", ".md", ".docx"].includes(extension)) {
      setError("Supported assignment files are .txt, .md, and .docx.");
      clearAssignmentFile();
      return;
    }
    if (file.size === 0) {
      setError("The selected assignment file is empty.");
      clearAssignmentFile();
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError("The selected assignment file is larger than 10 MB.");
      clearAssignmentFile();
      return;
    }
    setError(null);
    setSelectedAssignmentFile(file);
  }

  function showAssignmentAnalysisRun(run: ChatRunResponse) {
    setConversationId(run.conversation_id);
    const info = assignmentAnalysisInfoFromRun(run);
    const workspaceAction = run.action ? assignmentWorkspaceActionFromPayload(run.action) ?? undefined : undefined;
    const copilotResult = workspaceAction?.copilotResult;
    if (copilotResult) setAssignmentResult(copilotResult);
    setMessages((current) => [...current, {
      ...makeMessage("assistant", ""),
      createdAt: run.created_at,
      run,
      info,
      workspaceAction,
    }]);
  }

  function assignmentAnalysisInfoFromRun(run: ChatRunResponse): InfoCard | undefined {
    const card = run.action ? assignmentAnalysisFromActionPayload(run.action) : null;
    if (!card) return undefined;
    return {
      icon: "assignment",
      title: card.title,
      summary: card.summary,
      rows: card.rows,
      technical: card.technical,
    };
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
    const action = genericActionFromRun(run);
    const folderAction = run.action ? folderAccessActionFromPayload(run.action) ?? undefined : undefined;
    setMessages((current) => current.map((item) => item.id === assistantId ? {
      ...item,
      text: action || folderAction ? "" : run.assistant_response,
      createdAt: run.created_at,
      run,
      action: action ?? undefined,
      folderAction,
    } : item));
  }

  async function handleNativeRequest(prompt: string): Promise<boolean> {
    const normalized = normalize(prompt);
    if (awaitingAssignment) {
      setAwaitingAssignment(false);
      const looksLikePath = /\.(?:txt|md|docx)$/i.test(prompt) && !prompt.includes("\n");
      const run = await client.createChatAssignmentAnalysis(looksLikePath
        ? { path: prompt, selected_assignment: "all", conversation_id: conversationId, user_message: prompt }
        : { text: prompt, selected_assignment: "all", conversation_id: conversationId, user_message: prompt });
      showAssignmentAnalysisRun(run);
      return true;
    }
    if (normalized === "read this assignment" || normalized === "read an assignment") {
      setAwaitingAssignment(true);
      setMessages((current) => [...current, makeMessage("assistant", "Attach a .txt, .md, or .docx file here, paste the assignment text, or send a local file path in your next message.")]);
      return true;
    }
    if (isAssignmentWorkspaceRequest(normalized)) {
      if (!assignmentResult) {
        setMessages((current) => [...current, makeMessage(
          "assistant",
          "Read or attach an assignment first. I will then show the exact workspace plan for approval.",
        )]);
      } else {
        setMessages((current) => [...current, makeMessage("assistant", "The persisted workspace approval card is already in this conversation. Use Create workspace on that card when you are ready.")]);
      }
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

  function updateWorkspaceAction(
    messageId: string,
    change: (action: AssignmentWorkspaceAction) => AssignmentWorkspaceAction,
  ) {
    setMessages((current) => current.map((item) =>
      item.id === messageId && item.workspaceAction
        ? { ...item, workspaceAction: change(item.workspaceAction) }
        : item,
    ));
  }

  function updateFolderAction(
    messageId: string,
    change: (action: FolderAccessAction) => FolderAccessAction,
  ) {
    setMessages((current) => current.map((item) =>
      item.id === messageId && item.folderAction
        ? { ...item, folderAction: change(item.folderAction) }
        : item,
    ));
  }

  async function approveWorkspaceAction(
    messageId: string,
    action: AssignmentWorkspaceAction,
    chatRunId?: string,
  ) {
    if (action.status !== "awaiting_approval") return;
    if (!action.actionId || !chatRunId) return;
    const lockId = `assignment-workspace:${action.actionId}`;
    if (!action.targets.length || !tryLockCommandAction(locks.current, lockId)) return;

    try {
      updateWorkspaceAction(messageId, (current) => ({
        ...current,
        status: "running",
        error: undefined,
      }));
      await nextPaint();
      const updatedRun = await client.approveChatAssignmentWorkspace(action.actionId, {
        chat_run_id: chatRunId,
      });
      const updatedWorkspace = updatedRun.action
        ? assignmentWorkspaceActionFromPayload(updatedRun.action)
        : null;
      updateWorkspaceAction(messageId, (current) => ({
        ...current,
        ...(updatedWorkspace ?? {}),
      }));
      setMessages((current) => current.map((item) =>
        item.id === messageId ? { ...item, run: updatedRun } : item,
      ));
      const createdCount = updatedWorkspace?.results?.reduce(
        (total, result) => total + result.created_files.length,
        0,
      ) ?? 0;
      if (createdCount > 0) {
        setAssignmentResult((current) =>
          current === action.copilotResult ? { ...current, files_written: true } : current,
        );
      }
    } catch (caught) {
      updateWorkspaceAction(messageId, (current) => ({
        ...current,
        status: "failed",
        error: cleanError(caught),
      }));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function cancelWorkspaceAction(
    messageId: string,
    action: AssignmentWorkspaceAction,
    chatRunId?: string,
  ) {
    if (action.status !== "awaiting_approval") return;
    if (!action.actionId || !chatRunId) return;
    const lockId = `assignment-workspace:${action.actionId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      const updatedRun = await client.cancelChatAssignmentWorkspace(action.actionId, {
        chat_run_id: chatRunId,
      });
      const updatedWorkspace = updatedRun.action
        ? assignmentWorkspaceActionFromPayload(updatedRun.action)
        : null;
      updateWorkspaceAction(messageId, (current) => ({
        ...current,
        ...(updatedWorkspace ?? {}),
      }));
      setMessages((current) => current.map((item) =>
        item.id === messageId ? { ...item, run: updatedRun } : item,
      ));
    } catch (caught) {
      updateWorkspaceAction(messageId, (current) => ({ ...current, error: cleanError(caught) }));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function approveFolderAction(
    messageId: string,
    action: FolderAccessAction,
    chatRunId?: string,
  ) {
    if (action.status !== "awaiting_approval") return;
    if (!action.actionId || !chatRunId) return;
    const lockId = `folder-access:${action.actionId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      updateFolderAction(messageId, (current) => ({ ...current, status: "scanning", error: undefined }));
      await nextPaint();
      const updatedRun = await client.approveChatFolder(action.actionId, { chat_run_id: chatRunId });
      const updatedFolder = updatedRun.action
        ? folderAccessActionFromPayload(updatedRun.action)
        : null;
      updateFolderAction(messageId, (current) => ({ ...current, ...(updatedFolder ?? {}) }));
      setMessages((current) => current.map((item) =>
        item.id === messageId ? { ...item, run: updatedRun } : item,
      ));
    } catch (caught) {
      updateFolderAction(messageId, (current) => ({
        ...current,
        status: "failed",
        error: cleanError(caught),
      }));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function cancelFolderAction(
    messageId: string,
    action: FolderAccessAction,
    chatRunId?: string,
  ) {
    if (action.status !== "awaiting_approval") return;
    if (!action.actionId || !chatRunId) return;
    const lockId = `folder-access:${action.actionId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      const updatedRun = await client.cancelChatFolder(action.actionId, { chat_run_id: chatRunId });
      const updatedFolder = updatedRun.action
        ? folderAccessActionFromPayload(updatedRun.action)
        : null;
      updateFolderAction(messageId, (current) => ({ ...current, ...(updatedFolder ?? {}) }));
      setMessages((current) => current.map((item) =>
        item.id === messageId ? { ...item, run: updatedRun } : item,
      ));
    } catch (caught) {
      updateFolderAction(messageId, (current) => ({ ...current, error: cleanError(caught) }));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function rescanFolderAction(
    messageId: string,
    action: FolderAccessAction,
    chatRunId?: string,
  ) {
    if (action.status !== "completed") return;
    if (!action.actionId || !chatRunId) return;
    const lockId = `folder-rescan:${action.actionId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      updateFolderAction(messageId, (current) => ({ ...current, status: "scanning", error: undefined }));
      await nextPaint();
      const updatedRun = await client.rescanChatFolder(action.actionId, { chat_run_id: chatRunId });
      const updatedFolder = updatedRun.action
        ? folderAccessActionFromPayload(updatedRun.action)
        : null;
      updateFolderAction(messageId, (current) => ({ ...current, ...(updatedFolder ?? {}) }));
      setMessages((current) => current.map((item) =>
        item.id === messageId ? { ...item, run: updatedRun } : item,
      ));
    } catch (caught) {
      updateFolderAction(messageId, (current) => ({
        ...current,
        status: "failed",
        error: cleanError(caught),
      }));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function approveAction(
    messageId: string,
    action: ChatAction,
    chatRunId?: string,
  ) {
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
        association: {
          assignment_id: plan.assignment_id,
          workspace_path: plan.workspace || ".",
          ...(chatRunId ? { chat_run_id: chatRunId } : {}),
        },
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

  async function cancelAction(
    messageId: string,
    action: ChatAction,
    chatRunId?: string,
  ) {
    if (action.status !== "awaiting_approval") return;
    if (action.actionType === "system_configuration") {
      updateAction(messageId, (current) => ({ ...current, status: "cancelled", resultSummary: "Action cancelled. No setting was changed." }));
      return;
    }
    const plan = action.commandPlan;
    if (!plan || !tryLockCommandAction(locks.current, plan.plan_id)) return;
    try {
      const cancelled = await client.cancelAssignmentCommand(plan.plan_id, {
        assignment_id: plan.assignment_id,
        workspace_path: plan.workspace || ".",
        ...(chatRunId ? { chat_run_id: chatRunId } : {}),
      });
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
    setAssignmentResult(null);
    clearAssignmentFile();
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
          action: genericActionFromRun(run) ?? undefined,
          info: assignmentAnalysisInfoFromRun(run),
          workspaceAction: run.action ? assignmentWorkspaceActionFromPayload(run.action) ?? undefined : undefined,
          folderAction: run.action ? folderAccessActionFromPayload(run.action) ?? undefined : undefined,
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
          {messages.map((message) => <ChatMessage key={message.id} message={message} onApprove={approveAction} onCancel={cancelAction} onApproveWorkspace={approveWorkspaceAction} onCancelWorkspace={cancelWorkspaceAction} onApproveFolder={approveFolderAction} onCancelFolder={cancelFolderAction} onRescanFolder={rescanFolderAction} onOption={(option) => updateAction(message.id, (action) => ({ ...action, selectedOption: option }))} onContinue={continueConversation} />)}
          {loading && <div className="message assistant"><Avatar role="assistant" /><div className="bubble loading"><Activity className="spin" size={17} />Astra is working…</div></div>}
        </section>
        <form className="composer" onSubmit={submit}>
          {error && <div className="composer-error"><CircleAlert size={15} />{error}</div>}
          {selectedAssignmentFile && <div className="attachment-chip"><FileText size={16} /><span><strong>{selectedAssignmentFile.name}</strong><small>{formatFileSize(selectedAssignmentFile.size)}</small></span><button type="button" onClick={clearAssignmentFile} aria-label="Remove attached assignment"><X size={15} /></button></div>}
          <div className="composer-box">
            <input ref={assignmentFileInputRef} className="file-input" type="file" accept=".txt,.md,.docx" onChange={(event) => selectAssignmentFile(event.target.files?.[0] ?? null)} />
            <button type="button" className="attach-button" onClick={() => assignmentFileInputRef.current?.click()} disabled={loading} aria-label="Attach assignment file"><Paperclip size={18} /></button>
            <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder={selectedAssignmentFile ? "Add a message or send to analyse…" : "Message Astra…"} rows={1} disabled={loading} />
            <button className="send-button" disabled={(!input.trim() && !selectedAssignmentFile) || loading} aria-label="Send"><Send size={18} /></button>
          </div>
          <p>Approval required for execution. Unsupported and destructive actions remain blocked.</p>
        </form>
      </main>
    </div>
  );
}

function ChatMessage({
  message,
  onApprove,
  onCancel,
  onApproveWorkspace,
  onCancelWorkspace,
  onApproveFolder,
  onCancelFolder,
  onRescanFolder,
  onOption,
  onContinue,
}: {
  message: Message;
  onApprove: (id: string, action: ChatAction, chatRunId?: string) => Promise<void>;
  onCancel: (id: string, action: ChatAction, chatRunId?: string) => Promise<void>;
  onApproveWorkspace: (id: string, action: AssignmentWorkspaceAction, chatRunId?: string) => Promise<void>;
  onCancelWorkspace: (id: string, action: AssignmentWorkspaceAction, chatRunId?: string) => Promise<void>;
  onApproveFolder: (id: string, action: FolderAccessAction, chatRunId?: string) => Promise<void>;
  onCancelFolder: (id: string, action: FolderAccessAction, chatRunId?: string) => Promise<void>;
  onRescanFolder: (id: string, action: FolderAccessAction, chatRunId?: string) => Promise<void>;
  onOption: (option: string) => void;
  onContinue: (conversationId: string) => Promise<void>;
}) {
  return <article className={`message ${message.role}`}><Avatar role={message.role} /><div className="bubble">
    {message.text && <p className="message-text">{message.text}</p>}
    {message.action && <ActionCard action={message.action} onApprove={() => void onApprove(message.id, message.action!, message.run?.run_id)} onCancel={() => void onCancel(message.id, message.action!, message.run?.run_id)} onOption={onOption} />}
    {message.workspaceAction && <AssignmentWorkspaceCard action={message.workspaceAction} onApprove={() => void onApproveWorkspace(message.id, message.workspaceAction!, message.run?.run_id)} onCancel={() => void onCancelWorkspace(message.id, message.workspaceAction!, message.run?.run_id)} />}
    {message.folderAction && <FolderAccessCard action={message.folderAction} onApprove={() => void onApproveFolder(message.id, message.folderAction!, message.run?.run_id)} onCancel={() => void onCancelFolder(message.id, message.folderAction!, message.run?.run_id)} onRescan={() => void onRescanFolder(message.id, message.folderAction!, message.run?.run_id)} />}
    {message.info && <InfoCardView card={message.info} onContinue={onContinue} />}
    {message.run && !message.action && !message.folderAction && <RunDetails run={message.run} />}
  </div></article>;
}

function FolderAccessCard({
  action,
  onApprove,
  onCancel,
  onRescan,
}: {
  action: FolderAccessAction;
  onApprove: () => void;
  onCancel: () => void;
  onRescan: () => void;
}) {
  const busy = action.status === "scanning" || action.status === "approving" || action.status === "running";
  const completed = action.status === "completed";
  return <div className="action-card folder-action-card">
    <div className="card-heading"><div><span className="eyebrow">Project folder</span><h2>{completed ? "Project folder connected" : action.status === "cancelled" ? "Folder access cancelled" : "Folder access requested"}</h2></div><Status status={action.status as ChatActionStatus} /></div>
    <p>{completed ? "Astra scanned safe metadata only. File contents were not read or stored." : "Astra needs your approval before scanning this folder in read-only mode."}</p>
    <div className="folder-request"><FolderOpen size={17} /><span>{completed ? action.displayPath : action.requestedDisplayPath}</span></div>
    {action.status === "awaiting_approval" && <div className="button-row"><button className="primary-button" onClick={onApprove}><ShieldCheck size={16} />Approve read-only scan</button><button className="secondary-button danger" onClick={onCancel}><X size={16} />Cancel</button></div>}
    {busy && <div className="progress-line"><Activity className="spin" size={16} />Scanning approved folder metadata…</div>}
    {action.status === "cancelled" && <div className="result cancelled"><X size={17} />Folder access cancelled. No folder was scanned.</div>}
    {action.resultSummary && completed && <div className="result completed"><CheckCircle2 size={17} />{action.resultSummary}</div>}
    {completed && <div className="folder-summary-grid">
      <span><strong>{action.summary.readable}</strong> readable</span>
      <span><strong>{action.summary.ignored}</strong> ignored</span>
      <span><strong>{action.summary.assignments}</strong> assignments</span>
      <span><strong>{action.summary.datasets}</strong> datasets</span>
      <span><strong>{action.summary.sourceFiles}</strong> source files</span>
      <span><strong>{action.summary.reports}</strong> reports</span>
      <span><strong>{action.summary.evidenceFiles}</strong> evidence</span>
      <span><strong>{action.summary.configurationFiles}</strong> config</span>
    </div>}
    {completed && <div className="folder-diff"><span>Added {action.diff.added}</span><span>Changed {action.diff.changed}</span><span>Deleted {action.diff.deleted}</span><span>Unchanged {action.diff.unchanged}</span></div>}
    {completed && action.lastScannedAt && <p className="muted">Last scanned: {formatTime(action.lastScannedAt)}</p>}
    {completed && action.warnings.length > 0 && <div className="result failed"><CircleAlert size={17} /><div><strong>Scan warnings</strong><ul>{action.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div></div>}
    {completed && <div className="button-row"><button className="secondary-button" onClick={onRescan} disabled={busy}><RefreshCw size={16} />Rescan</button></div>}
    {action.status === "failed" && <div className="result failed"><CircleAlert size={17} /><div><strong>Folder scan failed</strong><pre>{action.error || "Astra could not scan this folder."}</pre></div></div>}
    {completed && <details className="technical folder-inventory-details"><summary><ChevronDown size={15} />Inventory ({action.inventory.length} items)</summary><div className="folder-inventory-list">{action.inventory.map((item) => <div key={item.relativePath} className={`folder-inventory-row ${item.status}`}><code>{item.relativePath}</code><span>{item.classification}</span><small>{item.status === "ignored" ? item.ignoreReason ?? "ignored" : formatFileSize(item.sizeBytes)}</small></div>)}</div></details>}
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body"><span>Mode: read-only metadata scan</span><span>Files are addressed by project-relative paths only.</span><JsonBlock value={{ status: action.status, summary: action.summary, diff: action.diff, warnings: action.warnings, scanCount: action.scanCount }} /></div></details>
  </div>;
}

function AssignmentWorkspaceCard({
  action,
  onApprove,
  onCancel,
}: {
  action: AssignmentWorkspaceAction;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const busy = ["approving", "approved", "running"].includes(action.status);
  return <div className="action-card workspace-action-card">
    <div className="card-heading"><div><span className="eyebrow">Assignment workspace</span><h2>Create assignment workspace?</h2></div><Status status={action.status} /></div>
    <p>Astra will write the planned starter code, documentation, evidence checklist, report outline, and configuration templates. No generated code will be executed.</p>
    <div className="workspace-plan-list">
      {action.targets.map((target) => <div key={`${target.assignmentNumber}:${target.workspacePath}`}>
        <span><strong>{target.assignmentTitle}</strong><code>{target.workspacePath}</code></span>
        <small>{target.plannedFileCount} planned files</small>
      </div>)}
    </div>
    {action.status === "awaiting_approval" && <div className="button-row"><button className="primary-button" onClick={onApprove}><ShieldCheck size={16} />Create workspace</button><button className="secondary-button danger" onClick={onCancel}><X size={16} />Cancel</button></div>}
    {busy && <div className="progress-line"><Activity className="spin" size={16} />{workspaceStatusText(action.status)}</div>}
    {action.resultSummary && <div className={`result ${action.status}`}><CheckCircle2 size={17} />{action.resultSummary}</div>}
    {action.error && <div className="result failed"><CircleAlert size={17} /><div><strong>Workspace creation failed</strong><pre>{action.error}</pre></div></div>}
    {action.results && <details className="technical workspace-technical"><summary><ChevronDown size={15} />Created workspace details</summary><div className="technical-body">
      {action.results.map((result) => <label key={result.workspace_path}>Location: {result.workspace_path}<pre>{result.created_files.length ? result.created_files.join("\n") : "(no new files)"}</pre>{result.skipped_files.length > 0 && <span>Skipped: {result.skipped_files.join(", ")}</span>}{result.refused_files.length > 0 && <span>Refused: {result.refused_files.join(", ")}</span>}</label>)}
    </div></details>}
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body"><span>Overwrite: disabled</span><span>Generated code execution: disabled</span><JsonBlock value={{ targets: action.targets, results: action.results ?? [] }} /></div></details>
  </div>;
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
  const Icon = card.icon === "history" ? History : card.icon === "database" ? Database : card.icon === "settings" ? ShieldCheck : card.icon === "assignment" ? FileText : Server;
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
function genericActionFromRun(run: ChatRunResponse) { return run.action?.action_type === "assignment" || run.action?.action_type === "folder_access" ? null : (run.action ? actionFromPayload(run.action) : null); }
function withoutPlan(details: Record<string, unknown>) { const rest = { ...details }; delete rest.command_plan; return rest; }
function statusText(status: ChatActionStatus) { return status === "approving" ? "Recording approval…" : status === "approved" ? "Approved. Starting…" : "Running the approved action…"; }
function workspaceStatusText(status: ChatActionStatus) { return status === "approving" ? "Recording approval…" : "Creating the approved workspace…"; }
function makeMessage(role: Message["role"], text: string): Message { return { id: newId(role), role, text, createdAt: new Date().toISOString() }; }
function newId(prefix: string) { return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
function normalize(value: string) { return value.trim().toLowerCase().replace(/[.!?]+$/, "").replace(/\s+/g, " "); }
function cleanError(error: unknown) { return error instanceof Error ? error.message : String(error); }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Saved chat" : date.toLocaleString(); }
function nextPaint() { return new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); }
function formatFileSize(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`; }
function loadSettings(): Settings { try { const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null") as Partial<Settings> | null; return { ...defaultSettings, ...(stored ?? {}) }; } catch { return defaultSettings; } }
