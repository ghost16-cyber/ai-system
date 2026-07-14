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
import { actionRunFromStreamEvent } from "./state/chatStreamState";
import {
  projectJobActionFromPayload,
  type ProjectJobAction,
} from "./state/projectJobState";
import {
  exactPlanApprovalRequest,
  projectDeliveryActionFromPayload,
  type ProjectDeliveryAction,
} from "./state/projectDeliveryState";

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
  jobAction?: ProjectJobAction;
  deliveryAction?: ProjectDeliveryAction;
  info?: InfoCard;
}

const SETTINGS_KEY = "astra.chat.settings";
const ACTIVE_CONVERSATION_KEY = "astra.chat.activeConversation";
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
  const restoredConversation = useRef(false);
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

  useEffect(() => {
    if (conversationId) localStorage.setItem(ACTIVE_CONVERSATION_KEY, conversationId);
  }, [conversationId]);

  useEffect(() => {
    if (restoredConversation.current) return;
    restoredConversation.current = true;
    const savedConversationId = localStorage.getItem(ACTIVE_CONVERSATION_KEY);
    if (!savedConversationId) return;
    setLoading(true);
    void client.getChatRuns(100).then((savedRuns) => {
      const runs = savedRuns.filter((run) => run.conversation_id === savedConversationId).reverse();
      setConversationId(savedConversationId);
      setMessages(restoreConversationMessages(runs, assignmentAnalysisInfoFromRun));
      setError(null);
    }).catch((caught) => setError(cleanError(caught))).finally(() => setLoading(false));
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
      if (event.event === "response_delta") {
        const delta = typeof event.data.delta === "string" ? event.data.delta : "";
        streamed += delta;
        setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, text: streamed } : item));
        return;
      }
      const actionRun = actionRunFromStreamEvent(event);
      if (!actionRun) return;
      setConversationId(actionRun.conversation_id);
      setMessages((current) => mergeProjectJobRun(current, actionRun, assistantId) ?? current.map((item) => item.id === assistantId ? {
          ...item,
          text: "",
          createdAt: actionRun.created_at,
          run: actionRun,
          action: genericActionFromRun(actionRun) ?? undefined,
          workspaceAction: actionRun.action ? assignmentWorkspaceActionFromPayload(actionRun.action) ?? undefined : undefined,
          folderAction: actionRun.action ? folderAccessActionFromPayload(actionRun.action) ?? undefined : undefined,
        } : item));
    });
    setConversationId(run.conversation_id);
    const action = genericActionFromRun(run);
    const folderAction = run.action ? folderAccessActionFromPayload(run.action) ?? undefined : undefined;
    setMessages((current) => mergeProjectJobRun(current, run, assistantId) ?? current.map((item) => item.id === assistantId ? {
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

  async function refreshProjectJob(jobId: string) {
    const job = await client.getProjectJob(jobId);
    const parsed = projectJobActionFromPayload({
      action_type: "project_job",
      technical_details: { project_job: job },
    });
    if (!parsed) return;
    setMessages((current) => current.map((item) =>
      item.jobAction?.jobId === jobId ? { ...item, jobAction: parsed } : item,
    ));
  }

  async function prepareProjectJob(job: ProjectJobAction) {
    if (!conversationId || !["planned", "blocked"].includes(job.status)) return;
    const lockId = `project-job-prepare:${job.jobId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    setMessages((current) => current.map((item) => item.jobAction?.jobId === job.jobId ? {
      ...item,
      jobAction: {
        ...item.jobAction,
        synthesis: {
          ...item.jobAction.synthesis,
          status: "preparing",
          strategy: item.jobAction.synthesis.strategy ?? "selecting_safe_strategy",
          summary: "Astra is checking deterministic synthesis first, then the configured controlled model only if needed.",
        },
      },
    } : item));
    try {
      const patchRun = await client.prepareProjectJob(job.jobId, conversationId);
      setMessages((current) => [...current, {
        ...makeMessage("assistant", ""),
        createdAt: patchRun.created_at,
        run: patchRun,
        action: genericActionFromRun(patchRun) ?? undefined,
      }]);
      await refreshProjectJob(job.jobId);
    } catch (caught) {
      setError(cleanError(caught));
      await refreshProjectJob(job.jobId).catch(() => undefined);
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function validateProjectJob(job: ProjectJobAction) {
    if (!conversationId || job.status !== "implementing") return;
    const lockId = `project-job-validation:${job.jobId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      const commandRun = await client.proposeProjectJobValidation(job.jobId, conversationId);
      setMessages((current) => [...current, {
        ...makeMessage("assistant", ""),
        createdAt: commandRun.created_at,
        run: commandRun,
        action: genericActionFromRun(commandRun) ?? undefined,
      }]);
      await refreshProjectJob(job.jobId);
    } catch (caught) {
      setError(cleanError(caught));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function cancelProjectJob(job: ProjectJobAction) {
    if (!conversationId || ["completed", "cancelled"].includes(job.status)) return;
    const lockId = `project-job-cancel:${job.jobId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      await client.cancelProjectJob(job.jobId, conversationId);
      await refreshProjectJob(job.jobId);
    } catch (caught) {
      setError(cleanError(caught));
    } finally {
      locks.current.delete(lockId);
    }
  }

  async function refreshProjectDelivery(deliveryJobId: string) {
    const delivery = await client.getProjectDelivery(deliveryJobId);
    const parsed = projectDeliveryActionFromPayload({
      action_type: "project_delivery", technical_details: { project_delivery: delivery },
    });
    if (!parsed) return;
    setMessages((current) => current.map((item) =>
      item.deliveryAction?.deliveryJobId === deliveryJobId ? { ...item, deliveryAction: parsed } : item,
    ));
  }

  async function approveDeliveryPlan(action: ProjectDeliveryAction) {
    if (!conversationId) return;
    const request = exactPlanApprovalRequest(action, conversationId);
    const lockId = `delivery-plan:${action.deliveryJobId}`;
    if (!request || !tryLockCommandAction(locks.current, lockId)) return;
    try {
      await client.approveProjectDeliveryPlan(action.deliveryJobId, conversationId, request.immutable_hash);
      await refreshProjectDelivery(action.deliveryJobId);
    } catch (caught) {
      setError(cleanError(caught));
      await refreshProjectDelivery(action.deliveryJobId).catch(() => undefined);
    } finally { locks.current.delete(lockId); }
  }

  async function prepareProjectDelivery(action: ProjectDeliveryAction) {
    if (!conversationId || action.status !== "plan_approved") return;
    const lockId = `delivery-prepare:${action.deliveryJobId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      const run = await client.prepareProjectDelivery(action.deliveryJobId, conversationId);
      setMessages((current) => [...current, {
        ...makeMessage("assistant", ""), createdAt: run.created_at, run,
        action: genericActionFromRun(run) ?? undefined,
      }]);
      await refreshProjectDelivery(action.deliveryJobId);
    } catch (caught) {
      setError(cleanError(caught));
      await refreshProjectDelivery(action.deliveryJobId).catch(() => undefined);
    } finally { locks.current.delete(lockId); }
  }

  async function verifyProjectDelivery(action: ProjectDeliveryAction) {
    if (!conversationId || action.status !== "patch_applied_not_verified") return;
    const criterion = action.criteria.find((item) => !["satisfied", "waived-by-user"].includes(item.state));
    if (!criterion) return;
    const lockId = `delivery-verify:${action.deliveryJobId}:${criterion.id}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      const run = await client.verifyProjectDelivery(action.deliveryJobId, conversationId, criterion.id);
      const generic = genericActionFromRun(run);
      if (generic) setMessages((current) => [...current, {
        ...makeMessage("assistant", ""), createdAt: run.created_at, run, action: generic,
      }]);
      await refreshProjectDelivery(action.deliveryJobId);
    } catch (caught) {
      setError(cleanError(caught));
    } finally { locks.current.delete(lockId); }
  }

  async function generateDeliveryHandoff(action: ProjectDeliveryAction) {
    if (!conversationId) return;
    const lockId = `delivery-handoff:${action.deliveryJobId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      await client.generateProjectDeliveryHandoff(action.deliveryJobId, conversationId);
      await refreshProjectDelivery(action.deliveryJobId);
    } catch (caught) { setError(cleanError(caught)); }
    finally { locks.current.delete(lockId); }
  }

  async function cancelProjectDelivery(action: ProjectDeliveryAction) {
    if (!conversationId || ["delivery_completed", "cancelled"].includes(action.status)) return;
    const lockId = `delivery-cancel:${action.deliveryJobId}`;
    if (!tryLockCommandAction(locks.current, lockId)) return;
    try {
      await client.cancelProjectDelivery(action.deliveryJobId, conversationId);
      await refreshProjectDelivery(action.deliveryJobId);
    } catch (caught) { setError(cleanError(caught)); }
    finally { locks.current.delete(lockId); }
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
    if (action.actionType === "project_patch") {
      const patchId = action.actionId;
      const jobId = projectJobIdFromAction(action);
      const deliveryId = projectDeliveryIdFromAction(action);
      if (!patchId || !chatRunId || !tryLockCommandAction(locks.current, `project-patch:${patchId}`)) return;
      try {
        updateAction(messageId, (current) => ({ ...current, status: "approving", error: undefined }));
        const approved = await client.approveProjectPatch(patchId, {
          chat_run_id: chatRunId,
          confirmation: `APPROVE PATCH ${patchId}`,
        });
        const approvedAction = approved.action ? actionFromPayload(approved.action) : null;
        updateAction(messageId, (current) => ({ ...current, ...(approvedAction ?? {}), status: "approved" }));
        await nextPaint();
        const applied = await client.applyProjectPatch(patchId, { chat_run_id: chatRunId });
        const appliedAction = applied.action ? actionFromPayload(applied.action) : null;
        updateAction(messageId, (current) => ({ ...current, ...(appliedAction ?? {}) }));
        setMessages((current) => current.map((item) => item.id === messageId ? { ...item, run: applied } : item));
        if (jobId) await refreshProjectJob(jobId);
        if (deliveryId) await refreshProjectDelivery(deliveryId);
      } catch (caught) {
        updateAction(messageId, (current) => ({ ...current, status: "failed", error: cleanError(caught) }));
      } finally { locks.current.delete(`project-patch:${patchId}`); }
      return;
    }
    if (action.actionType === "project_rollback") {
      const actionId = action.actionId;
      const patchId = actionId?.startsWith("rollback:") ? actionId.slice(9) : undefined;
      if (!patchId || !chatRunId || !tryLockCommandAction(locks.current, `project-rollback:${patchId}`)) return;
      try {
        updateAction(messageId, (current) => ({ ...current, status: "approving", error: undefined }));
        const restored = await client.approveProjectRollback(patchId, {
          chat_run_id: chatRunId,
          confirmation: `APPROVE ROLLBACK ${patchId}`,
        });
        const restoredAction = restored.action ? actionFromPayload(restored.action) : null;
        updateAction(messageId, (current) => ({ ...current, ...(restoredAction ?? {}) }));
        setMessages((current) => current.map((item) => item.id === messageId ? { ...item, run: restored } : item));
      } catch (caught) {
        updateAction(messageId, (current) => ({ ...current, status: "failed", error: cleanError(caught) }));
      } finally { locks.current.delete(`project-rollback:${patchId}`); }
      return;
    }
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
    const jobId = projectJobIdFromAction(action);
    const deliveryId = projectDeliveryIdFromAction(action);
    if (!plan || !tryLockCommandAction(locks.current, plan.plan_id)) return;
    try {
      updateAction(messageId, (current) => ({ ...current, status: "approving", error: undefined }));
      const result = await approveAndExecuteCommand({
        calls: {
          approve: (id, request) => action.actionType === "project_command"
            ? client.approveProjectCommand(id, request)
            : client.approveAssignmentCommand(id, request),
          execute: (id, request) => action.actionType === "project_command"
            ? client.executeProjectCommand(id, request)
            : client.executeAssignmentCommand(id, request),
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
      if (jobId) await refreshProjectJob(jobId);
      if (deliveryId) await refreshProjectDelivery(deliveryId);
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
    if (action.actionType === "project_patch" || action.actionType === "project_rollback") {
      const rawId = action.actionId;
      const jobId = projectJobIdFromAction(action);
      const deliveryId = projectDeliveryIdFromAction(action);
      const patchId = rawId?.startsWith("rollback:") ? rawId.slice(9) : rawId;
      if (!patchId || !chatRunId || !tryLockCommandAction(locks.current, `project-cancel:${rawId}`)) return;
      try {
        const updated = action.actionType === "project_patch"
          ? await client.rejectProjectPatch(patchId, { chat_run_id: chatRunId })
          : await client.rejectProjectRollback(patchId, { chat_run_id: chatRunId });
        const updatedAction = updated.action ? actionFromPayload(updated.action) : null;
        updateAction(messageId, (current) => ({ ...current, ...(updatedAction ?? {}) }));
        if (jobId) await refreshProjectJob(jobId);
        if (deliveryId) await refreshProjectDelivery(deliveryId);
      } catch (caught) {
        updateAction(messageId, (current) => ({ ...current, error: cleanError(caught) }));
      } finally { locks.current.delete(`project-cancel:${rawId}`); }
      return;
    }
    if (action.actionType === "system_configuration") {
      updateAction(messageId, (current) => ({ ...current, status: "cancelled", resultSummary: "Action cancelled. No setting was changed." }));
      return;
    }
    const plan = action.commandPlan;
    if (!plan || !tryLockCommandAction(locks.current, plan.plan_id)) return;
    try {
      const cancelled = await (action.actionType === "project_command"
        ? client.cancelProjectCommand(plan.plan_id, {
          assignment_id: plan.assignment_id,
          workspace_path: plan.workspace || ".",
          ...(chatRunId ? { chat_run_id: chatRunId } : {}),
        })
        : client.cancelAssignmentCommand(plan.plan_id, {
        assignment_id: plan.assignment_id,
        workspace_path: plan.workspace || ".",
        ...(chatRunId ? { chat_run_id: chatRunId } : {}),
      }));
      updateAction(messageId, (current) => ({ ...current, status: "cancelled", commandPlan: cancelled, resultSummary: "Action cancelled. No command was executed." }));
    } catch (caught) {
      updateAction(messageId, (current) => ({ ...current, error: cleanError(caught) }));
    } finally { locks.current.delete(plan.plan_id); }
  }

  function newChat() {
    localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
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
      const restored = restoreConversationMessages(runs, assignmentAnalysisInfoFromRun);
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
          {messages.map((message) => <ChatMessage key={message.id} message={message} onApprove={approveAction} onCancel={cancelAction} onApproveWorkspace={approveWorkspaceAction} onCancelWorkspace={cancelWorkspaceAction} onApproveFolder={approveFolderAction} onCancelFolder={cancelFolderAction} onRescanFolder={rescanFolderAction} onPrepareJob={prepareProjectJob} onValidateJob={validateProjectJob} onCancelJob={cancelProjectJob} onApproveDeliveryPlan={approveDeliveryPlan} onPrepareDelivery={prepareProjectDelivery} onVerifyDelivery={verifyProjectDelivery} onGenerateDeliveryHandoff={generateDeliveryHandoff} onCancelDelivery={cancelProjectDelivery} onOption={(option) => updateAction(message.id, (action) => ({ ...action, selectedOption: option }))} onContinue={continueConversation} />)}
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
  onPrepareJob,
  onValidateJob,
  onCancelJob,
  onApproveDeliveryPlan,
  onPrepareDelivery,
  onVerifyDelivery,
  onGenerateDeliveryHandoff,
  onCancelDelivery,
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
  onPrepareJob: (action: ProjectJobAction) => Promise<void>;
  onValidateJob: (action: ProjectJobAction) => Promise<void>;
  onCancelJob: (action: ProjectJobAction) => Promise<void>;
  onApproveDeliveryPlan: (action: ProjectDeliveryAction) => Promise<void>;
  onPrepareDelivery: (action: ProjectDeliveryAction) => Promise<void>;
  onVerifyDelivery: (action: ProjectDeliveryAction) => Promise<void>;
  onGenerateDeliveryHandoff: (action: ProjectDeliveryAction) => Promise<void>;
  onCancelDelivery: (action: ProjectDeliveryAction) => Promise<void>;
  onOption: (option: string) => void;
  onContinue: (conversationId: string) => Promise<void>;
}) {
  return <article className={`message ${message.role}`}><Avatar role={message.role} /><div className="bubble">
    {message.text && <p className="message-text">{message.text}</p>}
    {message.action && <ActionCard action={message.action} onApprove={() => void onApprove(message.id, message.action!, message.run?.run_id)} onCancel={() => void onCancel(message.id, message.action!, message.run?.run_id)} onOption={onOption} />}
    {message.workspaceAction && <AssignmentWorkspaceCard action={message.workspaceAction} onApprove={() => void onApproveWorkspace(message.id, message.workspaceAction!, message.run?.run_id)} onCancel={() => void onCancelWorkspace(message.id, message.workspaceAction!, message.run?.run_id)} />}
    {message.folderAction && <FolderAccessCard action={message.folderAction} onApprove={() => void onApproveFolder(message.id, message.folderAction!, message.run?.run_id)} onCancel={() => void onCancelFolder(message.id, message.folderAction!, message.run?.run_id)} onRescan={() => void onRescanFolder(message.id, message.folderAction!, message.run?.run_id)} />}
    {message.jobAction && <ProjectJobCard action={message.jobAction} onPrepare={() => void onPrepareJob(message.jobAction!)} onValidate={() => void onValidateJob(message.jobAction!)} onCancel={() => void onCancelJob(message.jobAction!)} />}
    {message.deliveryAction && <ProjectDeliveryCard action={message.deliveryAction} onApprovePlan={() => void onApproveDeliveryPlan(message.deliveryAction!)} onPrepare={() => void onPrepareDelivery(message.deliveryAction!)} onVerify={() => void onVerifyDelivery(message.deliveryAction!)} onHandoff={() => void onGenerateDeliveryHandoff(message.deliveryAction!)} onCancel={() => void onCancelDelivery(message.deliveryAction!)} />}
    {message.run && (message.run.source_paths?.length ?? 0) > 0 && <ProjectSources paths={message.run.source_paths ?? []} />}
    {message.info && <InfoCardView card={message.info} onContinue={onContinue} />}
    {message.run && !message.action && !message.folderAction && !message.jobAction && !message.deliveryAction && <RunDetails run={message.run} />}
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
    <p>{completed ? "Astra connected this project for bounded safe reading. Sensitive and excluded files remain blocked." : "Astra needs your approval before scanning and connecting this folder in read-only mode."}</p>
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
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body"><span>Mode: approved bounded project reading</span><span>Files are addressed by project-relative paths only; writes and commands require separate approvals.</span><JsonBlock value={{ status: action.status, summary: action.summary, diff: action.diff, warnings: action.warnings, scanCount: action.scanCount }} /></div></details>
  </div>;
}

function ProjectDeliveryCard({
  action, onApprovePlan, onPrepare, onVerify, onHandoff, onCancel,
}: {
  action: ProjectDeliveryAction;
  onApprovePlan: () => void;
  onPrepare: () => void;
  onVerify: () => void;
  onHandoff: () => void;
  onCancel: () => void;
}) {
  const terminal = ["delivery_completed", "cancelled"].includes(action.status);
  const handoffReady = action.progress.totalRequiredCriteria > 0
    && action.progress.satisfiedRequiredCriteria === action.progress.totalRequiredCriteria;
  const currentIndex = action.plan?.workUnits.findIndex((unit) => unit.id === action.activeWorkUnitId) ?? -1;
  return <div className="action-card project-delivery-card">
    <div className="card-heading"><div><span className="eyebrow">Project delivery</span><h2>{action.status === "delivery_completed" ? "Client-ready handoff" : "Bounded project task"}</h2></div><span className={`status status-${action.status}`}>{action.status.replace(/_/g, " ")}</span></div>
    <section className="job-section"><h3>Objective</h3><p>{action.objective}</p></section>
    <div className="delivery-progress" aria-label="Project delivery progress">
      <span><strong>{action.progress.completedWorkUnits} of {action.progress.totalWorkUnits}</strong> work units complete</span>
      <span><strong>{action.progress.satisfiedRequiredCriteria} of {action.progress.totalRequiredCriteria}</strong> required criteria satisfied</span>
      {currentIndex >= 0 && <span><strong>Work unit {currentIndex + 1} of {action.plan?.workUnits.length}</strong> active</span>}
    </div>
    {action.clarification?.question && action.status === "clarification_required" && <div className="job-clarification"><CircleAlert size={17} /><div><strong>One clarification is needed</strong><p>{action.clarification.question}</p><small>Reply in this conversation. No patch or command will run.</small></div></div>}
    <section className="job-section"><h3>Task specification</h3><div className="job-columns"><div><strong>In scope</strong><List items={action.requirements} /></div><div><strong>Deliverables</strong><List items={action.deliverables} /></div></div>
      <div className="criterion-list">{action.criteria.map((criterion) => <div className={`criterion criterion-${criterion.state}`} key={criterion.id}><span>{criterion.state === "satisfied" ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}</span><div><strong>{criterion.requirement}</strong><small>{criterion.required ? "Required" : "Optional"} · {criterion.verificationMode.replace(/_/g, " ")} · {criterion.state.replace(/-/g, " ")}</small>{criterion.blockedReason && <p>{criterion.blockedReason}</p>}</div></div>)}</div>
    </section>
    {action.plan && <section className="job-section"><div className="analysis-heading"><h3>Execution plan</h3><span className={`confidence confidence-${action.plan.confidence >= .8 ? "high" : action.plan.confidence >= .55 ? "medium" : "low"}`}>{Math.round(action.plan.confidence * 100)}% confidence</span></div>
      <div className="work-unit-list">{action.plan.workUnits.map((unit, index) => <div className={`work-unit work-unit-${unit.status}`} key={unit.id}><span>{index + 1}</span><div><strong>{unit.title}</strong><p>{unit.objective}</p><small>{unit.status.replace(/_/g, " ")}{unit.dependencies.length ? ` · after ${unit.dependencies.join(", ")}` : ""}</small></div></div>)}</div>
      <p className="muted">Plan approval permits preparation only. Every patch and executable command keeps its own approval.</p>
    </section>}
    {action.repair && <div className="result failed"><CircleAlert size={17} /><div><strong>Stage 8 diagnosis</strong><p>The failed verification is linked to a bounded diagnosis and repair cycle. Repair patch and rerun approvals remain separate.</p></div></div>}
    {action.scopeChanges.length > 0 && <div className="result failed"><CircleAlert size={17} /><div><strong>Scope change detected</strong><p>{action.scopeChanges[action.scopeChanges.length - 1]?.explanation}</p><small>The previous plan approval is invalid. Review the revised scope before continuing.</small></div></div>}
    {action.error && <div className="result failed"><CircleAlert size={17} /><div><strong>Delivery paused</strong><p>{action.error}</p></div></div>}
    {action.handoff && <section className="job-section handoff-card"><h3>Client handoff</h3><p><strong>{action.handoff.status.replace(/_/g, " ")}</strong></p><div className="job-columns"><div><strong>Changed files</strong><List items={action.handoff.changedFiles} /></div><div><strong>Verified validations</strong><List items={action.handoff.validations} /></div></div>{action.handoff.limitations.length > 0 && <div><strong>Known limitations</strong><List items={action.handoff.limitations} /></div>}{action.handoff.manualChecks.length > 0 && <div><strong>Manual checks still required</strong><List items={action.handoff.manualChecks} /></div>}<p className="muted">Rollback {action.handoff.rollbackAvailable ? "is available" : "is not available"} for applied Astra patches.</p></section>}
    <div className="button-row">
      {action.status === "awaiting_plan_approval" && <button className="primary-button" aria-label="Approve exact project delivery plan" onClick={onApprovePlan}><ShieldCheck size={16} />Approve plan</button>}
      {action.status === "plan_approved" && <button className="primary-button" onClick={onPrepare}><FileText size={16} />Prepare next patch</button>}
      {action.status === "patch_applied_not_verified" && <button className="primary-button" onClick={onVerify}><ShieldCheck size={16} />Verify next criterion</button>}
      {!action.handoff && (handoffReady || ["blocked", "awaiting_manual_verification", "partially_completed"].includes(action.status)) && <button className="secondary-button" onClick={onHandoff}><FileText size={16} />Prepare handoff</button>}
      {!terminal && <button className="secondary-button danger" onClick={onCancel}><X size={16} />Cancel delivery</button>}
    </div>
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body"><span>Specification source: {action.specificationSource}</span><span>Plan revision: {action.plan?.revision ?? "not ready"}</span><JsonBlock value={action.technical} /></div></details>
  </div>;
}

function ProjectJobCard({
  action,
  onPrepare,
  onValidate,
  onCancel,
}: {
  action: ProjectJobAction;
  onPrepare: () => void;
  onValidate: () => void;
  onCancel: () => void;
}) {
  const terminal = ["completed", "cancelled"].includes(action.status);
  const canPrepare = action.status === "planned"
    && action.revisionCount < action.maxRevisionCycles
    && !action.analysis.planOnly;
  const canValidate = action.status === "implementing" && action.validationPlan.length > 0;
  const completion = action.completionSummary;
  return <div className="action-card project-job-card">
    <div className="card-heading"><div><span className="eyebrow">Project job</span><h2>{action.status === "completed" ? "Client-ready completion" : "Integrated project work"}</h2></div><span className={`status status-${action.status}`}>{action.status.replace(/_/g, " ")}</span></div>
    <section className="job-section"><h3>Objective</h3><p>{action.objective}</p></section>
    {action.status === "needs_clarification" && action.clarification?.question && <div className="job-clarification"><CircleAlert size={17} /><div><strong>Clarification needed</strong><p>{action.clarification.question}</p><small>Reply naturally in this chat. Astra asks one focused question at a time.</small></div></div>}
    <section className="job-section"><h3>Requirements</h3><div className="job-columns"><div><strong>Deliverables</strong><List items={action.deliverables} /></div><div><strong>Acceptance criteria</strong><List items={action.acceptanceCriteria} /></div></div></section>
    <section className="job-section structural-analysis"><div className="analysis-heading"><h3>Analyzed project structure</h3><span className={`confidence confidence-${action.analysis.confidence}`}>{action.analysis.confidence} confidence</span></div>
      {action.analysis.findings.length > 0 && <div className="analysis-findings">{action.analysis.findings.slice(0, 8).map((finding) => <div key={finding.relativePath}><code>{finding.relativePath}</code><span>{finding.summary}</span><small>{finding.parseStatus}</small></div>)}</div>}
      {action.analysis.coherentFiles.length > 0 && <div><strong>Coherent file set</strong><div className="coherent-files">{action.analysis.coherentFiles.map((item) => <span className="coherent-file" key={item.relativePath}><code>{item.relativePath}</code><small>{item.classification.replace(/_/g, " ")} · {item.reason}</small></span>)}</div></div>}
      {action.analysis.symbols.length > 0 && <div><strong>Relevant symbols</strong><div className="symbol-list">{action.analysis.symbols.slice(0, 16).map((symbol, index) => <span key={`${symbol.relativePath}:${symbol.name}:${index}`}><code>{symbol.name}</code><small>{symbol.relativePath}{symbol.startLine ? `:${symbol.startLine}${symbol.endLine && symbol.endLine !== symbol.startLine ? `–${symbol.endLine}` : ""}` : ""} · {symbol.kind}</small></span>)}</div></div>}
      {action.analysis.impactedTests.length > 0 && <div><strong>Impacted tests</strong><div className="source-list">{action.analysis.impactedTests.map((path) => <code key={path}>{path}</code>)}</div></div>}
      {action.analysis.warnings.length > 0 && <div className="analysis-warning"><CircleAlert size={16} /><List items={action.analysis.warnings} /></div>}
      {action.analysis.planOnly && <div className="result failed"><CircleAlert size={17} /><div><strong>Plan-only safety stop</strong><List items={action.analysis.planOnlyReasons} /></div></div>}
      {action.analysis.prevalidation.status !== "not_started" && <div className={`prevalidation-status ${action.analysis.prevalidation.status}`}><strong>Pre-preview validation: {action.analysis.prevalidation.status}</strong><span>{action.analysis.prevalidation.checks.length} bounded checks completed before the immutable preview.</span>{action.analysis.prevalidation.warnings.length > 0 && <List items={action.analysis.prevalidation.warnings} />}</div>}
    </section>
    {action.synthesis.status !== "not_started" && <section className="job-section synthesis-status"><div className="analysis-heading"><h3>Implementation synthesis</h3><span className={`confidence confidence-${action.synthesis.confidence}`}>{action.synthesis.confidence} confidence</span></div>
      <p>{action.synthesis.summary ?? "A bounded synthesis attempt was recorded."}</p>
      <div className="synthesis-facts"><span><strong>Strategy</strong>{(action.synthesis.strategy ?? "unknown").replace(/_/g, " ")}</span><span><strong>Provider</strong>{action.synthesis.provider ?? "not invoked"}{action.synthesis.model ? ` / ${action.synthesis.model}` : ""}</span><span><strong>Evidence</strong>{action.synthesis.evidence.fileCount} files · {action.synthesis.evidence.excerptCount} excerpts</span></div>
      {action.synthesis.assumptions.length > 0 && <div><strong>Assumptions to review</strong><List items={action.synthesis.assumptions} /></div>}
      {action.synthesis.warnings.length > 0 && <div className="analysis-warning"><CircleAlert size={16} /><List items={action.synthesis.warnings} /></div>}
      {["provider_unavailable", "timeout", "malformed_or_unsafe", "confidence_rejected", "rejected"].includes(action.synthesis.status) && <div className="result failed"><CircleAlert size={17} /><div><strong>No preview created</strong><p>Project files were not modified. Refine the request or retry after the configured local model is available.</p></div></div>}
    </section>}
    {action.repair.status !== "not_started" && <section className={`job-section repair-status repair-${action.repair.status}`}>
      <div className="analysis-heading"><h3>Repair cycle {action.repair.cycleNumber || 1} of {action.repair.maxCycles}</h3><span className={`confidence confidence-${action.repair.confidence}`}>{action.repair.confidence} confidence</span></div>
      {action.repair.status === "offered" && <div className="result failed"><CircleAlert size={17} /><div><strong>Diagnosis available</strong><p>{action.repair.failedCommandSummary ?? "The approved validation command failed and bounded failure information was captured."}</p><p>I can analyse the failure and prepare a repair proposal. No files will change unless you approve a new patch.</p><small>Ask naturally in this chat to diagnose or repair the failed validation.</small></div></div>}
      {action.repair.status === "diagnosing" && <div className="progress-line"><Activity className="spin" size={16} />Fresh structural analysis and bounded diagnosis are in progress.</div>}
      {action.repair.status === "diagnosis_completed" && <div className="progress-line"><Activity className="spin" size={16} />Diagnosis completed. Preparing an immutable repair preview.</div>}
      {action.repair.redactionCount > 0 && <p className="muted">The failure output was redacted and limited before it could be sent to a coding model.</p>}
      {action.repair.outputTruncated && <div className="analysis-warning"><CircleAlert size={16} />The failure output was truncated to its bounded diagnostic limits.</div>}
      {action.repair.status === "needs_clarification" && action.repair.clarification?.question && <div className="job-clarification"><CircleAlert size={17} /><div><strong>Diagnosis needs clarification</strong><p>{action.repair.clarification.question}</p><small>Reply once in this conversation. No patch or command will run.</small></div></div>}
      {["plan_only", "repair_rejected", "limit_reached", "stale"].includes(action.repair.status) && <div className="result failed"><CircleAlert size={17} /><div><strong>{action.repair.status === "stale" ? "Failure evidence is stale" : action.repair.status === "limit_reached" ? "Repair cycle limit reached" : action.repair.status === "plan_only" ? "Diagnosis is plan-only" : "No repair preview created"}</strong><p>{action.repair.status === "stale" ? "The project changed after this failure was recorded, so the old diagnosis cannot be used." : "I am not confident enough to prepare a repair patch. No files or commands changed."}</p></div></div>}
      {action.repair.strategy && <div className="synthesis-facts"><span><strong>Diagnosis</strong>{action.repair.strategy.replace(/_/g, " ")}</span><span><strong>Provider</strong>{action.repair.provider ?? "not invoked"}{action.repair.model && action.repair.model !== "none" ? ` / ${action.repair.model}` : ""}</span><span><strong>Validation rerun</strong>{action.repair.validationRerunStatus.replace(/_/g, " ")}</span></div>}
      {action.repair.rootCauses.length > 0 && <div><strong>Likely root cause</strong><List items={action.repair.rootCauses.map((cause) => cause.explanation || cause.reasonCode)} /></div>}
      {action.repair.affectedFiles.length > 0 && <div><strong>Affected files</strong><div className="source-chips">{action.repair.affectedFiles.map((path) => <code key={path}>{path}</code>)}</div></div>}
      {action.repair.assumptions.length > 0 && <div><strong>Assumptions</strong><List items={action.repair.assumptions} /></div>}
      {action.repair.warnings.length > 0 && <div className="analysis-warning"><CircleAlert size={16} /><List items={action.repair.warnings} /></div>}
      {action.repair.status === "preview_ready" && <p>The repair is ready for review. It has not been applied and needs its own exact approval.</p>}
      {action.repair.status === "applied_not_validated" && <div className="result completed"><CheckCircle2 size={17} /><div><strong>Repair applied</strong><p>The repair was applied, but the validation command has not been rerun.</p></div></div>}
      {action.repair.status === "validation_planned" && <p>A new validation command is awaiting separate approval.</p>}
      {action.repair.status === "validated" && <div className="result completed"><CheckCircle2 size={17} />This repair cycle passed its separately approved validation.</div>}
      {action.repair.status === "rolled_back" && <p>The latest repair was rolled back to the immediately previous project state. No command was rerun.</p>}
    </section>}
    <section className="job-section"><h3>Plan</h3><ol className="project-plan-steps">{action.plan.steps.map((step) => <li key={step}>{step}</li>)}</ol>{action.plan.safetyImpact && <p className="muted">{action.plan.safetyImpact}</p>}</section>
    {action.relevantPaths.length > 0 && <ProjectSources paths={action.relevantPaths} />}
    {action.patchIds.length > 0 && <section className="job-section"><h3>Proposed changes</h3><p>{action.patchIds.length} immutable patch proposal{action.patchIds.length === 1 ? "" : "s"} linked to this job. Every patch retains its own approval.</p></section>}
    {action.status === "implementing" && <section className="job-section"><h3>Applied changes</h3><p>{action.repair.status === "applied_not_validated" ? "The repair was applied atomically. The validation command has not been rerun, and rollback is available." : "The approved patch was applied atomically. Tests have not run automatically, and rollback is available."}</p></section>}
    {action.validationResults.length > 0 && <section className="job-section"><h3>Validation</h3>{action.validationResults.map((result, index) => <div className={`validation-summary ${result.status === "passed" ? "passed" : "failed"}`} key={`${String(result.command_plan_id)}-${index}`}><strong>{String(result.status ?? "recorded")}</strong><span>{String(result.summary ?? "Bounded validation result recorded.")}</span></div>)}</section>}
    {action.status === "blocked" && action.repair.status === "not_started" && <div className="result failed"><CircleAlert size={17} /><div><strong>Controlled diagnosis</strong><p>{String(action.validationResults[action.validationResults.length - 1]?.recommended_next_step ?? "Review the bounded failure before requesting diagnosis.")}</p><small>No edit or command will repeat automatically.</small></div></div>}
    {completion && <section className="job-section completion-report"><h3>Completion</h3><dl><div><dt>Work completed</dt><dd>{joinSummary(completion.work_completed)}</dd></div><div><dt>Files changed</dt><dd>{joinSummary(completion.files_changed)}</dd></div><div><dt>Validation outcome</dt><dd>{String(completion.validation_outcome ?? "Not recorded")}</dd></div><div><dt>Verified tests</dt><dd>{joinSummary(completion.verified_facts, "None recorded")}</dd></div><div><dt>Assumptions</dt><dd>{joinSummary(completion.assumptions, "None recorded")}</dd></div><div><dt>Rollback</dt><dd>{completion.rollback_available === true ? "Available" : "Not available"}</dd></div><div><dt>Items not tested</dt><dd>{joinSummary(completion.items_not_tested, "None recorded")}</dd></div><div><dt>Manual checks</dt><dd>{joinSummary(completion.suggested_manual_checks)}</dd></div></dl></section>}
    <div className="button-row">
      {canPrepare && <button className="primary-button" onClick={onPrepare}><FileText size={16} />Prepare patch preview</button>}
      {canValidate && <button className="primary-button" onClick={onValidate}><ShieldCheck size={16} />Propose validation</button>}
      {!terminal && <button className="secondary-button danger" onClick={onCancel}><X size={16} />Cancel job</button>}
    </div>
    <details className="technical"><summary><ChevronDown size={15} />Technical details</summary><div className="technical-body"><span>Job: {action.jobId}</span><span>Revision limit: {action.revisionCount}/{action.maxRevisionCycles}</span><JsonBlock value={action.technical} /></div></details>
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
  const patch = projectPatchDetails(action);
  const rollback = projectRollbackDetails(action);
  const repair = projectRepairDetails(action);
  const busy = ["approving", "approved", "running"].includes(action.status);
  return <div className="action-card">
    <div className="card-heading"><div><span className="eyebrow">{action.actionType.replace(/_/g, " ")}</span><h2>{action.title}</h2></div><Status status={action.status} /></div>
    <p>{action.summary}</p>
    {plan && <div className="command-preview"><span>Command</span><code>{plan.command}</code></div>}
    {plan && <p className="muted">Working directory: <span className="friendly-location">Project workspace</span></p>}
    {repair && <div className="repair-preview-summary"><strong>Repair cycle {repair.cycleNumber}</strong><span>{repair.strategy.replace(/_/g, " ")} diagnosis · {repair.confidence} confidence</span><p>{repair.rootCauseSummary}</p>{repair.affectedFiles.length > 0 && <div className="source-chips">{repair.affectedFiles.map((path) => <code key={path}>{path}</code>)}</div>}</div>}
    {action.actionType === "project_plan" && <ol className="project-plan-steps">{action.steps.map((step) => <li key={step}>{step}</li>)}</ol>}
    {patch && <div className="patch-preview"><div className="patch-summary"><span><strong>{patch.changes.length}</strong> files</span><span><strong>+{patch.additions}</strong> additions</span><span><strong>-{patch.deletions}</strong> deletions</span></div>{patch.changes.map((change) => <details key={change.relative_path} className="patch-file"><summary><ChevronDown size={15} /><code>{change.relative_path}</code><span>{change.operation}</span></summary><p>{change.explanation}</p><pre className="patch-diff">{change.unified_diff || "No textual diff."}</pre>{change.diff_truncated && <small>Diff preview truncated.</small>}</details>)}<p className="muted">Nothing has been changed yet. Tests require separate approval.</p></div>}
    {rollback && <div className="rollback-preview"><strong>Files to restore</strong><div className="source-chips">{rollback.relative_paths.map((path) => <code key={path}>{path}</code>)}</div></div>}
    {action.options && <label className="model-choice">Model profile<select value={action.selectedOption} onChange={(event) => onOption(event.target.value)} disabled={busy}>{action.options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>}
    {action.status === "awaiting_approval" && <div className="button-row"><button className="primary-button" onClick={onApprove}><ShieldCheck size={16} />{action.actionType === "command" || action.actionType === "project_command" ? "Approve and run" : action.actionType === "project_patch" ? "Approve and apply patch" : action.actionType === "project_rollback" ? "Approve rollback" : "Approve change"}</button><button className="secondary-button danger" onClick={onCancel}><X size={16} />Cancel</button></div>}
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

function ProjectSources({ paths }: { paths: string[] }) {
  return <div className="project-sources"><span>Project evidence</span><div className="source-chips">{paths.slice(0, 12).map((path) => <code key={path}>{path}</code>)}</div></div>;
}

function List({ items }: { items: string[] }) {
  return items.length ? <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted">None identified.</p>;
}

function Welcome({ onPrompt }: { onPrompt: (prompt: string) => void }) {
  return <div className="welcome"><span className="welcome-icon"><Bot size={30} /></span><h1>What can I help with?</h1><p>Ask a question, inspect Astra, or request an allowlisted action directly in chat.</p><div className="suggestions">{["Run the tests", "Show system status", "Show recent chats", "What model are you using?"].map((prompt) => <button key={prompt} onClick={() => onPrompt(prompt)}>{prompt}</button>)}</div></div>;
}

function Avatar({ role }: { role: "user" | "assistant" }) { return <div className="avatar">{role === "user" ? "You" : <Bot size={17} />}</div>; }
function Status({ status }: { status: ChatActionStatus }) { return <span className={`status status-${status}`}>{status.replace(/_/g, " ")}</span>; }
function JsonBlock({ value }: { value: unknown }) { return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>; }
function genericActionFromRun(run: ChatRunResponse) { return ["assignment", "folder_access", "project_job", "project_delivery"].includes(String(run.action?.action_type)) ? null : (run.action ? actionFromPayload(run.action) : null); }
function mergeProjectJobRun(current: Message[], run: ChatRunResponse, assistantId: string): Message[] | null {
  const incomingDelivery = run.action ? projectDeliveryActionFromPayload(run.action) : null;
  if (incomingDelivery) {
    const existing = current.find((item) => item.id !== assistantId && item.deliveryAction?.deliveryJobId === incomingDelivery.deliveryJobId);
    return current.map((item) => {
      if (existing && item.id === existing.id) return { ...item, deliveryAction: incomingDelivery };
      if (item.id !== assistantId) return item;
      return {
        ...item, text: existing ? run.assistant_response : "", createdAt: run.created_at, run,
        deliveryAction: existing ? undefined : incomingDelivery, action: undefined,
        workspaceAction: undefined, folderAction: undefined, jobAction: undefined,
      };
    });
  }
  const incoming = run.action ? projectJobActionFromPayload(run.action) : null;
  if (!incoming) return null;
  const existing = current.find((item) => item.id !== assistantId && item.jobAction?.jobId === incoming.jobId);
  return current.map((item) => {
    if (existing && item.id === existing.id) return { ...item, jobAction: incoming };
    if (item.id !== assistantId) return item;
    return {
      ...item,
      text: existing ? run.assistant_response : "",
      createdAt: run.created_at,
      run,
      jobAction: existing ? undefined : incoming,
      action: undefined,
      workspaceAction: undefined,
      folderAction: undefined,
    };
  });
}
function restoreConversationMessages(
  runs: ChatRunResponse[],
  assignmentInfo: (run: ChatRunResponse) => InfoCard | undefined,
): Message[] {
  const latestJobs = new Map<string, ProjectJobAction>();
  const latestDeliveries = new Map<string, ProjectDeliveryAction>();
  for (const run of runs) {
    const job = run.action ? projectJobActionFromPayload(run.action) : null;
    if (job) latestJobs.set(job.jobId, job);
    const delivery = run.action ? projectDeliveryActionFromPayload(run.action) : null;
    if (delivery) latestDeliveries.set(delivery.deliveryJobId, delivery);
  }
  const renderedJobs = new Set<string>();
  const renderedDeliveries = new Set<string>();
  return runs.flatMap<Message>((run) => {
    const job = run.action ? projectJobActionFromPayload(run.action) : null;
    const showJob = job && !renderedJobs.has(job.jobId);
    if (showJob) renderedJobs.add(job.jobId);
    const restoredJob = showJob ? latestJobs.get(job.jobId) : undefined;
    const delivery = run.action ? projectDeliveryActionFromPayload(run.action) : null;
    const showDelivery = delivery && !renderedDeliveries.has(delivery.deliveryJobId);
    if (showDelivery) renderedDeliveries.add(delivery.deliveryJobId);
    const restoredDelivery = showDelivery ? latestDeliveries.get(delivery.deliveryJobId) : undefined;
    return [
      { ...makeMessage("user", run.user_message), createdAt: run.created_at },
      {
        ...makeMessage("assistant", restoredJob || restoredDelivery || genericActionFromRun(run) || (run.action && !job && !delivery) ? "" : run.assistant_response),
        createdAt: run.created_at,
        run,
        action: genericActionFromRun(run) ?? undefined,
        info: assignmentInfo(run),
        workspaceAction: run.action ? assignmentWorkspaceActionFromPayload(run.action) ?? undefined : undefined,
        folderAction: run.action ? folderAccessActionFromPayload(run.action) ?? undefined : undefined,
        jobAction: restoredJob,
        deliveryAction: restoredDelivery,
      },
    ];
  });
}
function projectJobIdFromAction(action: ChatAction): string | undefined {
  const patch = action.technicalDetails.project_patch;
  if (patch && typeof patch === "object" && !Array.isArray(patch) && typeof (patch as Record<string, unknown>).job_id === "string") return (patch as Record<string, unknown>).job_id as string;
  const scope = action.technicalDetails.project_scope;
  if (scope && typeof scope === "object" && !Array.isArray(scope) && typeof (scope as Record<string, unknown>).job_id === "string") return (scope as Record<string, unknown>).job_id as string;
  return undefined;
}
function projectDeliveryIdFromAction(action: ChatAction): string | undefined {
  const patch = action.technicalDetails.project_patch;
  if (patch && typeof patch === "object" && !Array.isArray(patch) && typeof (patch as Record<string, unknown>).delivery_job_id === "string") return (patch as Record<string, unknown>).delivery_job_id as string;
  const scope = action.technicalDetails.project_scope;
  if (scope && typeof scope === "object" && !Array.isArray(scope) && typeof (scope as Record<string, unknown>).delivery_job_id === "string") return (scope as Record<string, unknown>).delivery_job_id as string;
  return undefined;
}
function joinSummary(value: unknown, fallback = "None"): string {
  return Array.isArray(value) ? value.map(String).join(", ") || fallback : typeof value === "string" && value ? value : fallback;
}
function withoutPlan(details: Record<string, unknown>) { const rest = { ...details }; delete rest.command_plan; return rest; }
function projectPatchDetails(action: ChatAction) {
  const value = action.technicalDetails.project_patch;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const patch = value as Record<string, unknown>;
  const changes = Array.isArray(patch.changes) ? patch.changes.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)).map((item) => ({
    relative_path: typeof item.relative_path === "string" ? item.relative_path : "unknown",
    operation: typeof item.operation === "string" ? item.operation : "modify",
    explanation: typeof item.explanation === "string" ? item.explanation : "Proposed project change.",
    unified_diff: typeof item.unified_diff === "string" ? item.unified_diff : "",
    diff_truncated: item.diff_truncated === true,
  })) : [];
  return { changes, additions: typeof patch.additions === "number" ? patch.additions : 0, deletions: typeof patch.deletions === "number" ? patch.deletions : 0 };
}
function projectRollbackDetails(action: ChatAction) {
  const value = action.technicalDetails.project_rollback;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const rollback = value as Record<string, unknown>;
  return { relative_paths: Array.isArray(rollback.relative_paths) ? rollback.relative_paths.filter((item): item is string => typeof item === "string") : [] };
}

function projectRepairDetails(action: ChatAction) {
  const value = action.technicalDetails.project_repair;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const repair = value as Record<string, unknown>;
  return {
    cycleNumber: typeof repair.cycle_number === "number" ? repair.cycle_number : 0,
    strategy: typeof repair.diagnosis_strategy === "string" ? repair.diagnosis_strategy : "bounded",
    confidence: typeof (repair.confidence as Record<string, unknown> | undefined)?.level === "string" ? String((repair.confidence as Record<string, unknown>).level) : "unknown",
    rootCauseSummary: typeof repair.root_cause_summary === "string" ? repair.root_cause_summary : "A bounded diagnosis supports this repair scope.",
    affectedFiles: Array.isArray(repair.affected_files) ? repair.affected_files.filter((item): item is string => typeof item === "string" && !item.startsWith("/") && !item.includes("../")) : [],
  };
}
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
