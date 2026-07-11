import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  ClipboardCheck,
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
  type AssignmentCopilotRequest,
  type AssignmentCopilotResult,
  type AssignmentCodeWriteResult,
  type AssignmentDatasetMapping,
  type AssignmentManifestWriteResult,
  type ChatStreamEvent,
  type ChatTraceEntry,
  type ChatRunResponse,
  type HealthData,
  type IntelligenceDashboardResponse,
  type RagEvaluationStatusResponse,
  type RagStatusResponse,
  type RawHistoryItem,
  type RawJob,
  type RawTool,
  type SelectedSlmResponse,
  type SlmProfilesResponse,
  type SlmStatusResponse,
  type TrainingDatasetStatus,
  type TrainingExample,
  type TrainingLabel,
  type TrainingLabelRequest,
  type TrainingExamplesResponse,
  type UsefulnessRating,
} from "./clients/astraClient";
import type {
  CompactTraceResponse,
  RuntimeContext,
  SpecialistDashboard,
  SpecialistModelsResponse,
  SpecialistTracesResponse,
  TraceEvent,
} from "./types/contracts";
import { AssignmentExecutionSection } from "./components/AssignmentExecutionSection";
import { AssignmentEvidenceReadinessSection } from "./components/AssignmentEvidenceReadinessSection";

type PageId = "chat" | "assignments" | "system" | "history" | "settings";
type SafetyMode = "read_only" | "confirm";
type AssignmentSelection = "all" | "1" | "2" | "3";

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

interface ChatProgressStep {
  id: string;
  label: string;
  detail: string;
  status: "active" | "done" | "warning" | "error";
}

interface SystemData {
  health: HealthData | null;
  runtime: RuntimeContext | null;
  selectedSlm: SelectedSlmResponse | null;
  slmProfiles: SlmProfilesResponse | null;
  slmStatus: SlmStatusResponse | null;
  rag: RagStatusResponse | null;
  ragEvaluation: RagEvaluationStatusResponse | null;
  trainingStatus: TrainingDatasetStatus | null;
  trainingExamples: TrainingExamplesResponse | null;
  specialistDashboard: SpecialistDashboard | null;
  specialistModels: SpecialistModelsResponse | null;
  intelligenceDashboard: IntelligenceDashboardResponse | null;
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
  { id: "assignments", label: "Assignments", icon: ClipboardCheck },
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
  const [chatProgress, setChatProgress] = useState<ChatProgressStep[]>([]);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);
  const [systemData, setSystemData] = useState<SystemData | null>(null);
  const [systemLoading, setSystemLoading] = useState(true);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [ragIndexLoading, setRagIndexLoading] = useState(false);
  const [ragIndexNotice, setRagIndexNotice] = useState<string | null>(null);
  const [ragEvaluationLoading, setRagEvaluationLoading] = useState(false);
  const [ragEvaluationNotice, setRagEvaluationNotice] = useState<string | null>(null);
  const [trainingNotice, setTrainingNotice] = useState<string | null>(null);
  const [trainingActionLoading, setTrainingActionLoading] = useState(false);
  const [historyData, setHistoryData] = useState<HistoryData | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<CompactTraceResponse | null>(null);
  const [settingsNotice, setSettingsNotice] = useState<string | null>(null);
  const [assignmentText, setAssignmentText] = useState("");
  const [assignmentPath, setAssignmentPath] = useState("");
  const [assignmentSelection, setAssignmentSelection] = useState<AssignmentSelection>("all");
  const [assignmentWorkspacePath, setAssignmentWorkspacePath] = useState("");
  const [assignmentDatasetPath, setAssignmentDatasetPath] = useState("");
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [assignmentError, setAssignmentError] = useState<string | null>(null);
  const [assignmentResult, setAssignmentResult] = useState<AssignmentCopilotResult | null>(null);
  const [assignmentExportLoading, setAssignmentExportLoading] = useState(false);
  const [assignmentExportNotice, setAssignmentExportNotice] = useState<string | null>(null);
  const [assignmentCreateLoading, setAssignmentCreateLoading] = useState(false);
  const [assignmentOverwrite, setAssignmentOverwrite] = useState(false);
  const [assignmentCodeWriteResult, setAssignmentCodeWriteResult] = useState<AssignmentCodeWriteResult | null>(null);
  const [assignmentManifestWriteResult, setAssignmentManifestWriteResult] = useState<AssignmentManifestWriteResult | null>(null);
  const [assignmentDatasetMapping, setAssignmentDatasetMapping] = useState<AssignmentDatasetMapping | null>(null);

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
      ragEvaluation,
      trainingStatus,
      trainingExamples,
      specialistDashboard,
      specialistModels,
      intelligenceDashboard,
      tools,
    ] = await Promise.all([
      settle(client.getHealth()),
      settle(client.getRuntimeContext()),
      settle(client.getSelectedSlm()),
      settle(client.getSlmProfiles()),
      settle(client.getSlmStatus()),
      settle(client.getRagStatus()),
      settle(client.getRagEvaluationStatus()),
      settle(client.getTrainingDatasetStatus()),
      settle(client.getTrainingExamples(8)),
      settle(client.getSpecialistDashboard()),
      settle(client.getSpecialistModels()),
      settle(client.getIntelligenceDashboard()),
      settle(client.getTools()),
    ]);

    setSystemData({
      health: health.value,
      runtime: runtime.value,
      selectedSlm: selectedSlm.value,
      slmProfiles: slmProfiles.value,
      slmStatus: slmStatus.value,
      rag: rag.value,
      ragEvaluation: ragEvaluation.value,
      trainingStatus: trainingStatus.value,
      trainingExamples: trainingExamples.value,
      specialistDashboard: specialistDashboard.value,
      specialistModels: specialistModels.value,
      intelligenceDashboard: intelligenceDashboard.value,
      tools: tools.value ?? [],
    });

    const firstError = [
      health.error,
      runtime.error,
      selectedSlm.error,
      slmProfiles.error,
      slmStatus.error,
      rag.error,
      ragEvaluation.error,
      trainingStatus.error,
      trainingExamples.error,
      specialistDashboard.error,
      specialistModels.error,
      intelligenceDashboard.error,
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
    setChatProgress([]);
    setChatLoading(true);
    const request = {
      message: prompt,
      use_rag: settings.ragEnabled,
      safety_mode: settings.safetyMode,
      conversation_id: activeConversationId,
    };
    const userMessage: ChatMessage = {
      id: newId("user"),
      role: "user",
      text: prompt,
      createdAt: new Date().toISOString(),
    };
    const assistantId = newId("assistant-stream");
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      createdAt: new Date().toISOString(),
    };
    setMessages((current) => [...current, userMessage, assistantMessage]);

    try {
      let streamedText = "";
      const run = await client.streamChat(request, (event) => {
        if (event.event === "response_delta") {
          const delta = readString(event.data.delta);
          streamedText += delta;
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, text: streamedText || "Astra is drafting the response..." }
                : message,
            ),
          );
          return;
        }
        const progress = progressFromStreamEvent(event);
        if (progress) {
          setChatProgress((current) => [...current, progress]);
        }
        if (event.event === "run_started") {
          const conversationId = readString(event.data.conversation_id);
          if (conversationId) setActiveConversationId(conversationId);
        }
      });
      setActiveConversationId(run.conversation_id);
      const assistantText =
        readString(run.assistant_response) ||
        "Astra completed the request, but the backend did not return response text.";
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, text: assistantText, createdAt: run.created_at, meta: run }
            : message,
        ),
      );
      setSelectedRunId(`chat:${run.run_id}`);
      setChatProgress((current) => [
        ...current,
        {
          id: `completed-${run.run_id}`,
          label: "Run completed",
          detail: "Final response saved to chat history.",
          status: "done",
        },
      ]);
      void refreshHistory();
    } catch (streamError) {
      setChatProgress((current) => [
        ...current,
        {
          id: `fallback-${Date.now()}`,
          label: "Streaming fallback",
          detail: `Live stream failed, using stable chat run: ${cleanError(streamError)}`,
          status: "warning",
        },
      ]);
      try {
        const run = await client.runChat(request);
        setActiveConversationId(run.conversation_id);
        const assistantText =
          readString(run.assistant_response) ||
          "Astra completed the request, but the backend did not return response text.";
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId
              ? { ...message, text: assistantText, createdAt: run.created_at, meta: run }
              : message,
          ),
        );
        setSelectedRunId(`chat:${run.run_id}`);
        void refreshHistory();
      } catch (error) {
        const message = cleanError(error);
        setChatError(message);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  text: `I could not reach the live backend: ${message}`,
                  createdAt: new Date().toISOString(),
                }
              : item,
          ),
        );
      }
    } finally {
      setChatLoading(false);
    }
  }

  function startNewChat() {
    setActiveConversationId(null);
    setMessages([]);
    setChatError(null);
    setChatProgress([]);
    setLastPrompt(null);
    setSelectedRunId(null);
  }

  async function rebuildRagIndex() {
    setRagIndexLoading(true);
    setRagIndexNotice(null);
    try {
      const result = await client.rebuildRagIndex();
      setRagIndexNotice(
        `Indexed ${result.indexed_files} file(s) into ${result.indexed_chunks} chunk(s).`,
      );
      await refreshSystem();
    } catch (error) {
      setRagIndexNotice(`Could not rebuild project index: ${cleanError(error)}`);
    } finally {
      setRagIndexLoading(false);
    }
  }

  async function runRagEvaluation() {
    setRagEvaluationLoading(true);
    setRagEvaluationNotice(null);
    try {
      const result = await client.runRagEvaluation();
      if (result.status === "index_missing") {
        setRagEvaluationNotice(result.message ?? "Build the project index before running evaluation.");
      } else {
        setRagEvaluationNotice(
          `RAG evaluation finished: ${result.passed_cases}/${result.total_cases} case(s) passed.`,
        );
      }
      await refreshSystem();
    } catch (error) {
      setRagEvaluationNotice(`Could not run RAG evaluation: ${cleanError(error)}`);
    } finally {
      setRagEvaluationLoading(false);
    }
  }

  async function labelTrainingExample(exampleId: string, request: TrainingLabelRequest) {
    setTrainingActionLoading(true);
    setTrainingNotice(null);
    try {
      const result = await client.labelTrainingExample(exampleId, request);
      setTrainingNotice(`Updated ${result.example.label_status} label for ${result.example.id.slice(0, 12)}.`);
      await refreshSystem();
    } catch (error) {
      setTrainingNotice(`Could not update training example: ${cleanError(error)}`);
    } finally {
      setTrainingActionLoading(false);
    }
  }

  async function exportTrainingDataset(format: "jsonl" | "csv") {
    setTrainingActionLoading(true);
    setTrainingNotice(null);
    try {
      const result = await client.exportTrainingDataset(format);
      setTrainingNotice(`Exported ${result.row_count} reviewed example(s) to ${result.path}.`);
      await refreshSystem();
    } catch (error) {
      setTrainingNotice(`Could not export training dataset: ${cleanError(error)}`);
    } finally {
      setTrainingActionLoading(false);
    }
  }

  function resetLocalState() {
    localStorage.removeItem(SETTINGS_KEY);
    setMessages([]);
    setSelectedRunId(null);
    setSettings(defaultSettings);
    setSettingsNotice("Frontend settings and the visible chat transcript were reset.");
  }

  async function runAssignmentCopilot() {
    const text = assignmentText.trim();
    const path = assignmentPath.trim();
    if (!text && !path) {
      setAssignmentError("Paste assignment text or provide a local document path.");
      return;
    }
    setAssignmentLoading(true);
    setAssignmentError(null);
    const request: AssignmentCopilotRequest = {
      selected_assignment: assignmentSelection,
    };
    if (text) request.text = text;
    if (path) request.path = path;
    if (assignmentWorkspacePath.trim()) {
      request.workspace_path = assignmentWorkspacePath.trim();
    }
    if (assignmentDatasetPath.trim()) {
      request.dataset_path = assignmentDatasetPath.trim();
    }
    try {
      const result = await client.runAssignmentCopilot(request);
      setAssignmentResult(result);
      setAssignmentCodeWriteResult(null);
      setAssignmentManifestWriteResult(null);
      setAssignmentExportNotice(null);
      if (result.dataset_profile) {
        setAssignmentDatasetMapping(await client.mapAssignmentDataset({ dataset_profile: result.dataset_profile }));
      } else {
        setAssignmentDatasetMapping(null);
      }
    } catch (error) {
      setAssignmentError(assignmentFriendlyError(error, { documentPath: path, datasetPath: assignmentDatasetPath.trim(), workspacePath: assignmentWorkspacePath.trim() }));
    } finally {
      setAssignmentLoading(false);
    }
  }

  async function exportAssignmentReportPackage() {
    const text = assignmentText.trim();
    const path = assignmentPath.trim();
    if (!text && !path) {
      setAssignmentExportNotice("Paste assignment text or provide a document path before exporting.");
      return;
    }
    setAssignmentExportLoading(true);
    setAssignmentExportNotice(null);
    try {
      const result = await client.exportAssignmentReport({
        text: text || undefined,
        path: path || undefined,
        assignment_number: assignmentSelection === "all" ? 1 : Number(assignmentSelection),
        workspace_path: assignmentWorkspacePath.trim() || undefined,
        report_folder: "report_package",
        overwrite: false,
      });
      setAssignmentExportNotice(
        `Report package: ${result.created_files.length} created, ${result.skipped_files.length} skipped in ${result.output_directory}.`,
      );
    } catch (error) {
      setAssignmentExportNotice(assignmentFriendlyError(error, { documentPath: path, workspacePath: assignmentWorkspacePath.trim() }));
    } finally {
      setAssignmentExportLoading(false);
    }
  }

  async function createAssignmentStarterFiles() {
    if (!assignmentResult) {
      setAssignmentExportNotice("Run Assignment Copilot before creating starter files.");
      return;
    }
    if (!assignmentWorkspacePath.trim()) {
      setAssignmentExportNotice("Provide an approved workspace path before creating starter files.");
      return;
    }
    setAssignmentCreateLoading(true);
    setAssignmentExportNotice(null);
    try {
      const result = await client.writeAssignmentCode({
        workspace_path: assignmentWorkspacePath.trim(),
        blueprints: assignmentResult.code_blueprints ?? [],
        overwrite: assignmentOverwrite,
      });
      setAssignmentCodeWriteResult(result);
      setAssignmentExportNotice(
        `Starter files: ${result.created_files.length} created, ${result.skipped_files.length} skipped, ${result.refused_files.length} refused.`,
      );
    } catch (error) {
      setAssignmentExportNotice(assignmentFriendlyError(error, { workspacePath: assignmentWorkspacePath.trim(), datasetPath: assignmentDatasetPath.trim() }));
    } finally {
      setAssignmentCreateLoading(false);
    }
  }

  async function writeAssignmentManifestFile() {
    if (!assignmentResult) {
      setAssignmentExportNotice("Run Assignment Copilot before writing a manifest.");
      return;
    }
    if (!assignmentWorkspacePath.trim()) {
      setAssignmentExportNotice("Provide an approved workspace path before writing a manifest.");
      return;
    }
    setAssignmentCreateLoading(true);
    setAssignmentExportNotice(null);
    try {
      const result = await client.writeAssignmentManifest({
        workspace_path: assignmentWorkspacePath.trim(),
        copilot_result: assignmentResult,
        assignment_number: assignmentSelection === "all" ? 1 : Number(assignmentSelection),
        dataset_path: assignmentDatasetPath.trim() || undefined,
        document_path: assignmentPath.trim() || undefined,
        overwrite: assignmentOverwrite,
      });
      setAssignmentManifestWriteResult(result);
      setAssignmentExportNotice(result.written ? `Manifest written to ${result.manifest_path}.` : "Manifest was not written.");
    } catch (error) {
      setAssignmentExportNotice(assignmentFriendlyError(error, { workspacePath: assignmentWorkspacePath.trim() }));
    } finally {
      setAssignmentCreateLoading(false);
    }
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
            progress={chatProgress}
            lastPrompt={lastPrompt}
            onSubmit={() => void sendChat()}
            onRetry={() => lastPrompt && void sendChat(lastPrompt)}
            onNewChat={startNewChat}
            activeConversationId={activeConversationId}
            settings={settings}
            runtime={systemData?.runtime ?? null}
          />
        )}
        {activePage === "assignments" && (
          <AssignmentCopilotPage
            client={client}
            text={assignmentText}
            setText={setAssignmentText}
            path={assignmentPath}
            setPath={setAssignmentPath}
            selection={assignmentSelection}
            setSelection={setAssignmentSelection}
            workspacePath={assignmentWorkspacePath}
            setWorkspacePath={setAssignmentWorkspacePath}
            datasetPath={assignmentDatasetPath}
            setDatasetPath={setAssignmentDatasetPath}
            loading={assignmentLoading}
            error={assignmentError}
            result={assignmentResult}
            exportLoading={assignmentExportLoading}
            exportNotice={assignmentExportNotice}
            createLoading={assignmentCreateLoading}
            overwrite={assignmentOverwrite}
            setOverwrite={setAssignmentOverwrite}
            codeWriteResult={assignmentCodeWriteResult}
            manifestWriteResult={assignmentManifestWriteResult}
            datasetMapping={assignmentDatasetMapping}
            onSubmit={() => void runAssignmentCopilot()}
            onExport={() => void exportAssignmentReportPackage()}
            onCreateStarterFiles={() => void createAssignmentStarterFiles()}
            onWriteManifest={() => void writeAssignmentManifestFile()}
          />
        )}
        {activePage === "system" && (
          <SystemPage
            data={systemData}
            loading={systemLoading}
            error={systemError}
            settings={settings}
            ragIndexLoading={ragIndexLoading}
            ragIndexNotice={ragIndexNotice}
            ragEvaluationLoading={ragEvaluationLoading}
            ragEvaluationNotice={ragEvaluationNotice}
            trainingNotice={trainingNotice}
            trainingActionLoading={trainingActionLoading}
            onRefresh={() => void refreshSystem()}
            onRebuildRagIndex={() => void rebuildRagIndex()}
            onRunRagEvaluation={() => void runRagEvaluation()}
            onLabelTrainingExample={(exampleId, request) => void labelTrainingExample(exampleId, request)}
            onExportTrainingDataset={(format) => void exportTrainingDataset(format)}
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
  progress,
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
  progress: ChatProgressStep[];
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
              <div className="message-body">
                <div className="loading-row">
                  <Activity size={16} className="spin" />
                  Calling backend, routing specialist, checking safety...
                </div>
                <LiveProgress steps={progress} />
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
  const sources = ragSources(run);
  return (
    <div className="result-meta">
      <Metric label="Specialist" value={run.selected_specialist || "Not routed"} />
      <Metric label="Intent" value={`${run.intent || "unknown"} / ${Math.round((run.confidence ?? 0) * 100)}%`} />
      <Metric
        label="RAG"
        value={ragDisplay(run)}
        tone={run.rag_used ? "green" : "blue"}
      />
      <Metric
        label="Grounding"
        value={groundingDisplay(run)}
        tone={groundingTone(run.grounding_status)}
      />
      <Metric label="Sources" value={String(run.source_count ?? sources.length)} />
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
      {sources.length > 0 && (
        <div className="source-list">
          <strong>RAG sources</strong>
          {sources.map((source) => (
            <span key={`${source.path}-${source.startLine}-${source.endLine}`}>
              {formatSource(source)}
            </span>
          ))}
        </div>
      )}
      <div className="meta-reason">
        <TraceTimeline events={traceSummaryToEvents(run.trace_summary ?? [])} />
      </div>
    </div>
  );
}

function AssignmentCopilotPage({
  client,
  text,
  setText,
  path,
  setPath,
  selection,
  setSelection,
  workspacePath,
  setWorkspacePath,
  datasetPath,
  setDatasetPath,
  loading,
  error,
  result,
  exportLoading,
  exportNotice,
  createLoading,
  overwrite,
  setOverwrite,
  codeWriteResult,
  manifestWriteResult,
  datasetMapping,
  onSubmit,
  onExport,
  onCreateStarterFiles,
  onWriteManifest,
}: {
  client: HttpAstraClient;
  text: string;
  setText: (value: string) => void;
  path: string;
  setPath: (value: string) => void;
  selection: AssignmentSelection;
  setSelection: (value: AssignmentSelection) => void;
  workspacePath: string;
  setWorkspacePath: (value: string) => void;
  datasetPath: string;
  setDatasetPath: (value: string) => void;
  loading: boolean;
  error: string | null;
  result: AssignmentCopilotResult | null;
  exportLoading: boolean;
  exportNotice: string | null;
  createLoading: boolean;
  overwrite: boolean;
  setOverwrite: (value: boolean) => void;
  codeWriteResult: AssignmentCodeWriteResult | null;
  manifestWriteResult: AssignmentManifestWriteResult | null;
  datasetMapping: AssignmentDatasetMapping | null;
  onSubmit: () => void;
  onExport: () => void;
  onCreateStarterFiles: () => void;
  onWriteManifest: () => void;
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  const summary = asObject(result?.parsed_document_summary);
  const tasks = (result?.extracted_assignment_sections ?? []).flatMap((section) =>
    readObjectArray(asObject(section).tasks).map((task) => ({
      assignment: readString(asObject(section).title, "Assignment"),
      title: readString(task.title, "Task"),
      output: readString(task.required_output, "Completed evidence"),
    })),
  );
  const planItems = readObjectArray(asObject(result?.action_plan).checklist);
  const starterFiles = (result?.recommended_starter_files ?? []).flatMap((plan) =>
    readObjectArray(asObject(plan).files).map((file) => ({
      assignment: String(readNumber(asObject(plan).assignment_number)),
      path: readString(file.file_path),
      purpose: readString(file.purpose),
    })),
  );
  const evidenceItems = readObjectArray(asObject(result?.evidence_checklist).items);
  const evidenceSummary = asObject(asObject(result?.evidence_checklist).summary);
  const evidenceByAssignment = groupObjectsByString(evidenceItems, "assignment_name");
  const evidenceTypeCounts = asObject(evidenceSummary.by_evidence_type);
  const reportMarkdown = readString(asObject(result?.report_draft).markdown);
  const datasetProfile = asObject(result?.dataset_profile);
  const suitability = asObject(datasetProfile.suitability);
  const workspacePlans = result?.workspace_build_plans ?? [];
  const runbooks = result?.runbooks ?? [];
  const codeBlueprints = (result?.code_blueprints ?? []).flatMap((set) =>
    readObjectArray(asObject(set).blueprints),
  );
  const analysisQuestions = (result?.analysis_plans ?? []).flatMap((plan) =>
    readObjectArray(asObject(plan).questions),
  );
  const dashboardSpecs = result?.dashboard_specs ?? [];
  const finalReadiness = asObject(result?.final_readiness);
  const generatedFileTree = codeBlueprints.map((blueprint) => readString(blueprint.file_path, "Blueprint file"));
  const controlledWriteCompleted = Boolean(
    codeWriteResult?.created_files.length ||
    codeWriteResult?.skipped_files.length ||
    manifestWriteResult?.written ||
    manifestWriteResult?.skipped,
  );

  return (
    <section className="page-stack">
      <PageTitle
        eyebrow="Assignment Copilot"
        title="Plan, evidence, report, and readiness"
        detail="Paste an assignment brief or provide a local document path. Analysis is non-executing; validation requires separate Plan, Approve, and Execute actions."
      />

      <form className="panel copilot-form" onSubmit={submit}>
        <div className="form-grid">
          <label>
            Assignment text
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Paste the Big Data assignment brief here..."
            />
          </label>
          <div className="form-stack">
            <label>
              Local document path
              <input
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="assignment.md or docs/brief.docx"
              />
              <span className="helper-text">Supports .txt, .md, and .docx. On WSL, Windows files should use /mnt/c/...</span>
            </label>
            <label>
              Assignment
              <select
                value={selection}
                onChange={(event) => setSelection(event.target.value as AssignmentSelection)}
              >
                <option value="all">All assignments</option>
                <option value="1">Assignment 1</option>
                <option value="2">Assignment 2</option>
                <option value="3">Assignment 3</option>
              </select>
            </label>
            <label>
              Workspace path
              <input
                value={workspacePath}
                onChange={(event) => setWorkspacePath(event.target.value)}
                placeholder="Optional, relative to backend workspace"
              />
              <span className="helper-text">Use an assignment workspace folder. Nothing runs until a command is separately planned, approved, and executed.</span>
            </label>
            <label>
              Dataset path
              <input
                value={datasetPath}
                onChange={(event) => setDatasetPath(event.target.value)}
                placeholder="Optional, e.g. data/events.csv or data/events.tsv"
              />
              <span className="helper-text">Supports .csv, .txt, and .tsv with comma, semicolon, tab, or pipe delimiters.</span>
            </label>
            <button className="primary-button" type="submit" disabled={loading}>
              <ClipboardCheck size={16} />
              {loading ? "Running..." : "Run copilot analysis"}
            </button>
          </div>
        </div>
        {error && <Notice tone="amber" text={error} />}
        {exportNotice && <Notice tone="blue" text={exportNotice} />}
      </form>

      {!result && !loading && (
        <EmptyState
          icon={ClipboardCheck}
          title="No assignment analysis yet"
          detail="Run the copilot to generate the action plan, starter files, evidence checklist, report draft, and marking readiness."
        />
      )}

      {result && (
        <>
          <div className="overview-grid">
            <StatusCard
              icon={ClipboardCheck}
              title="Document"
              value={readString(summary.title, "Assignment brief")}
              detail={`${readNumber(summary.section_count)} section(s) detected`}
              tone="neutral"
            />
            <StatusCard
              icon={CheckCircle2}
              title="Evidence"
              value={`${readNumber(evidenceSummary.missing_count)} missing`}
              detail={`${readNumber(evidenceSummary.total_required)} required item(s)`}
              tone={readNumber(evidenceSummary.missing_count) ? "amber" : "green"}
            />
            <StatusCard
              icon={Wrench}
              title="Safety"
              value={controlledWriteCompleted ? "Controlled write completed" : result.tools_executed ? "Executed" : "Guidance only"}
              detail={controlledWriteCompleted ? "No commands executed" : result.files_written ? "Files written" : "No files written"}
              tone={result.tools_executed || result.files_written ? "red" : "green"}
            />
            <StatusCard
              icon={Sparkles}
              title="Next step"
              value="Recommended"
              detail={result.next_recommended_step}
              tone="green"
            />
          </div>

          <section className="panel">
            <div className="panel-title-row">
              <PanelTitle icon={Wrench} title="Controlled Creation" />
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={overwrite}
                  onChange={(event) => setOverwrite(event.target.checked)}
                />
                Overwrite existing files
              </label>
            </div>
            <div className="notice subtle">
              <ShieldCheck size={16} />
              Astra can write starter files, report package files, and a manifest only after you click a button. No commands are executed and no credentials are generated.
            </div>
            <div className="button-row">
              <button className="secondary-button" onClick={onCreateStarterFiles} disabled={createLoading || !workspacePath.trim()}>
                {createLoading ? <Activity size={16} className="spin" /> : <Wrench size={16} />}
                Create starter files
              </button>
              <button className="secondary-button" onClick={onExport} disabled={exportLoading || !workspacePath.trim()}>
                {exportLoading ? <Activity size={16} className="spin" /> : <ClipboardCheck size={16} />}
                Write report package
              </button>
              <button className="secondary-button" onClick={onWriteManifest} disabled={createLoading || !workspacePath.trim()}>
                {createLoading ? <Activity size={16} className="spin" /> : <Database size={16} />}
                Write manifest
              </button>
            </div>
            {!workspacePath.trim() && <EmptyInline text="Enter an approved workspace path before creating files." />}
            <section className="two-column creation-details">
              <div>
                <h3 className="section-subtitle">Generated file tree</h3>
                <div className="compact-list">
                  {generatedFileTree.map((file) => (
                    <div key={file}>
                      <strong>{file}</strong>
                      <span>Blueprint starter file</span>
                    </div>
                  ))}
                  {!generatedFileTree.length && <EmptyInline text="Run analysis to see generated files." />}
                </div>
              </div>
              <div>
                <h3 className="section-subtitle">Dataset mapping</h3>
                {datasetMapping ? (
                  <InfoList
                    items={[
                      ["Timestamp", mappingColumn(datasetMapping.timestamp_column)],
                      ["Primary numeric", mappingColumn(datasetMapping.primary_numeric_indicator)],
                      ["Category/filter", mappingColumn(datasetMapping.category_grouping_column)],
                      ["Threshold idea", datasetMapping.classification_threshold_idea],
                      ["Spark columns", datasetMapping.spark_aggregation_columns.join(", ") || "Placeholders"],
                      ["Snowflake tables", datasetMapping.snowflake_table_names.join(", ")],
                      ["Redis keys", datasetMapping.redis_key_patterns.join(", ")],
                    ]}
                  />
                ) : (
                  <EmptyInline text="Add a dataset path and run analysis to see mapping suggestions." />
                )}
              </div>
            </section>
            {codeWriteResult && (
              <CreationResult
                title="Starter file result"
                created={codeWriteResult.created_files}
                skipped={codeWriteResult.skipped_files}
                refused={codeWriteResult.refused_files}
                warnings={codeWriteResult.warnings}
                nextSteps={codeWriteResult.next_manual_steps}
              />
            )}
            {manifestWriteResult && (
              <InfoList
                items={[
                  ["Manifest", manifestWriteResult.written ? "written" : manifestWriteResult.skipped ? "skipped" : "not written"],
                  ["Path", manifestWriteResult.manifest_path],
                  ["Overwrite", manifestWriteResult.overwrite ? "true" : "false"],
                  ["Warnings", manifestWriteResult.warnings.join(", ") || "None"],
                ]}
              />
            )}
          </section>

          <AssignmentExecutionSection
            client={client}
            assignmentId={selection === "all" ? null : `assignment-${selection}`}
            workspacePath={workspacePath}
          />

          <AssignmentEvidenceReadinessSection
            client={client}
            assignmentId={selection === "all" ? null : `assignment-${selection}`}
            workspacePath={workspacePath}
            assignmentOutput={{
              evidence_checklist: result.evidence_checklist,
              action_plan: result.action_plan,
            }}
          />

          <section className="panel">
            <PanelTitle icon={ClipboardCheck} title="Assignment Tasks" />
            <div className="compact-list">
              {tasks.map((task, index) => (
                <div key={`${task.assignment}-${task.title}-${index}`}>
                  <strong>{task.title}</strong>
                  <span>{task.assignment} / {task.output}</span>
                </div>
              ))}
              {!tasks.length && <EmptyInline text="No tasks detected." />}
            </div>
          </section>

          <section className="two-column">
            <div className="panel">
              <PanelTitle icon={Wrench} title="Generated Action Plan" />
              <div className="compact-list">
                {planItems.slice(0, 10).map((item, index) => (
                  <div key={`${readString(item.task_id)}-${index}`}>
                    <strong>{readString(item.title, "Checklist item")}</strong>
                    <span>{readString(item.group)} / {readString(item.status, "todo")}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="panel">
              <PanelTitle icon={Database} title="Starter Files" />
              <div className="compact-list">
                {starterFiles.map((file) => (
                  <div key={`${file.assignment}-${file.path}`}>
                    <strong>{file.path}</strong>
                    <span>Assignment {file.assignment} / {file.purpose}</span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="two-column">
            <div className="panel">
              <PanelTitle icon={ShieldCheck} title="Evidence Checklist" />
              <div className="mini-metrics">
                {Object.entries(evidenceTypeCounts).map(([type, count]) => (
                  <Metric key={type} label={type} value={String(count)} />
                ))}
              </div>
              <div className="compact-list">
                {Object.entries(evidenceByAssignment).map(([assignment, items]) => (
                  <div key={assignment}>
                    <strong>{assignment}</strong>
                    <span>{items.length} evidence item(s)</span>
                    <div className="nested-evidence">
                      {items.slice(0, 4).map((item) => (
                        <span key={readString(item.evidence_id)}>
                          {readString(item.title, "Evidence")} / {readString(item.evidence_type)} / {readString(item.status)}
                        </span>
                      ))}
                      {items.length > 4 && <em>{items.length - 4} more item(s)</em>}
                    </div>
                  </div>
                ))}
                {!evidenceItems.length && <EmptyInline text="No evidence items returned." />}
              </div>
            </div>
            <div className="panel">
              <PanelTitle icon={Send} title="Safe Commands" />
              <div className="compact-list">
                {result.safe_next_commands.map((command) => (
                  <div key={readString(command.command)}>
                    <strong>{readString(command.command)}</strong>
                    <span>{readString(command.risk_level)} risk / {readString(command.purpose)}</span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="two-column">
            <div className="panel">
              <PanelTitle icon={Database} title="Dataset Suitability" />
              {result.dataset_profile ? (
                <div className="compact-list">
                  <div>
                    <strong>{readString(datasetProfile.dataset_path, "Dataset")}</strong>
                    <span>
                      {readNumber(datasetProfile.row_count_estimate)} row estimate / {readNumber(datasetProfile.column_count)} columns
                    </span>
                  </div>
                  <div>
                    <strong>Recommended use</strong>
                    <span>{readString(suitability.recommended_assignment_use, "none")}</span>
                  </div>
                  <div>
                    <strong>Checks</strong>
                    <span>
                      A1 {readBoolean(suitability.assignment_1_suitable) ? "yes" : "no"} / A2 {readBoolean(suitability.assignment_2_suitable) ? "yes" : "no"} / A3 {readBoolean(suitability.assignment_3_suitable) ? "yes" : "no"}
                    </span>
                  </div>
                </div>
              ) : (
                <EmptyInline text="Add a dataset path before running analysis to see suitability checks." />
              )}
            </div>
            <div className="panel">
              <PanelTitle icon={Wrench} title="Workspace Build Plan" />
              <div className="compact-list">
                {workspacePlans.map((plan) => {
                  const entry = asObject(plan);
                  const files = readObjectArray(entry.files_to_create);
                  return (
                    <div key={readString(entry.assignment_name)}>
                      <strong>{readString(entry.assignment_name, "Assignment")}</strong>
                      <span>{files.length} file(s) to create / {readString(entry.dataset_copy_or_reference_plan)}</span>
                    </div>
                  );
                })}
                {!workspacePlans.length && <EmptyInline text="No workspace build plan returned." />}
              </div>
            </div>
          </section>

          <section className="two-column">
            <div className="panel">
              <PanelTitle icon={Activity} title="Marking Readiness" />
              <div className="compact-list">
                {result.marking_readiness.map((item) => {
                  const entry = asObject(item);
                  const missing = readStringArray(entry.missing_critical_items);
                  return (
                    <div key={readString(entry.assignment_name)}>
                      <strong>{readString(entry.assignment_name, "Assignment")}</strong>
                      <span>
                        Estimated ready: {readNumber(entry.estimated_ready_marks)} / {readNumber(entry.total_marks_available)} marks
                      </span>
                      {missing.length > 0 && <em>Missing: {missing.slice(0, 3).join(", ")}</em>}
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="panel">
              <PanelTitle icon={MessageSquareText} title="Report Outline" />
              <pre className="report-preview">{reportMarkdown.slice(0, 2600)}</pre>
              <button className="secondary-button export-button" onClick={onExport} disabled={exportLoading}>
                <ClipboardCheck size={16} />
                {exportLoading ? "Exporting..." : "Export markdown package"}
              </button>
            </div>
          </section>

          <section className="panel">
            <PanelTitle icon={Clock3} title="Runbook" />
            <div className="compact-list">
              {runbooks.flatMap((runbook) =>
                readObjectArray(asObject(runbook).steps).slice(0, 6).map((step) => (
                  <div key={`${readString(asObject(runbook).title)}-${readString(step.step_id)}`}>
                    <strong>{readString(step.title, "Runbook step")}</strong>
                    <span>{readString(step.expected_result)}{readString(step.screenshot_to_take) ? ` / Screenshot: ${readString(step.screenshot_to_take)}` : ""}</span>
                  </div>
                )),
              )}
              {!runbooks.length && <EmptyInline text="No runbook returned." />}
            </div>
          </section>

          <section className="two-column">
            <div className="panel">
              <PanelTitle icon={Wrench} title="Code Blueprints" />
              <div className="compact-list">
                {codeBlueprints.slice(0, 10).map((blueprint) => (
                  <div key={readString(blueprint.file_path)}>
                    <strong>{readString(blueprint.file_path, "Blueprint file")}</strong>
                    <span>{readString(blueprint.technology_area)} / {readString(blueprint.purpose)}</span>
                  </div>
                ))}
                {!codeBlueprints.length && <EmptyInline text="No code blueprints returned." />}
              </div>
            </div>
            <div className="panel">
              <PanelTitle icon={MessageSquareText} title="Business Questions" />
              <div className="compact-list">
                {analysisQuestions.slice(0, 10).map((question) => (
                  <div key={readString(question.question_id)}>
                    <strong>{readString(question.question, "Analysis question")}</strong>
                    <span>{readString(question.method)} / {readString(question.suggested_logic)}</span>
                  </div>
                ))}
                {!analysisQuestions.length && <EmptyInline text="No analysis plan returned." />}
              </div>
            </div>
          </section>

          <section className="two-column">
            <div className="panel">
              <PanelTitle icon={Database} title="Dashboard Specification" />
              <div className="compact-list">
                {dashboardSpecs.map((spec) => {
                  const entry = asObject(spec);
                  return (
                    <div key={readString(entry.dashboard_title)}>
                      <strong>{readString(entry.dashboard_title, "Dashboard")}</strong>
                      <span>{readString(entry.dashboard_type)} / {readString(entry.data_source)}</span>
                    </div>
                  );
                })}
                {!dashboardSpecs.length && <EmptyInline text="No dashboard specification returned." />}
              </div>
            </div>
            <div className="panel">
              <PanelTitle icon={CheckCircle2} title="Final Readiness" />
              {result.final_readiness ? (
                <div className="compact-list">
                  <div>
                    <strong>{readString(finalReadiness.readiness_level, "in_progress")}</strong>
                    <span>{readString(finalReadiness.next_best_action)}</span>
                  </div>
                  {readStringArray(finalReadiness.missing_blockers).map((blocker) => (
                    <div key={blocker}>
                      <strong>Missing blocker</strong>
                      <span>{blocker}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyInline text="No final readiness report returned." />
              )}
            </div>
          </section>
        </>
      )}
    </section>
  );
}

function SystemPage({
  data,
  loading,
  error,
  settings,
  ragIndexLoading,
  ragIndexNotice,
  ragEvaluationLoading,
  ragEvaluationNotice,
  trainingNotice,
  trainingActionLoading,
  onRefresh,
  onRebuildRagIndex,
  onRunRagEvaluation,
  onLabelTrainingExample,
  onExportTrainingDataset,
}: {
  data: SystemData | null;
  loading: boolean;
  error: string | null;
  settings: FrontendSettings;
  ragIndexLoading: boolean;
  ragIndexNotice: string | null;
  ragEvaluationLoading: boolean;
  ragEvaluationNotice: string | null;
  trainingNotice: string | null;
  trainingActionLoading: boolean;
  onRefresh: () => void;
  onRebuildRagIndex: () => void;
  onRunRagEvaluation: () => void;
  onLabelTrainingExample: (exampleId: string, request: TrainingLabelRequest) => void;
  onExportTrainingDataset: (format: "jsonl" | "csv") => void;
}) {
  const runtime = data?.runtime;
  const selectedProfile = data?.selectedSlm?.profile ?? {};
  const modelCounts = data?.specialistDashboard?.models_by_status ?? {};
  const projectIndex = data?.rag?.project_index;
  const evaluation = data?.ragEvaluation?.latest_evaluation ?? null;
  const trainingStatus = data?.trainingStatus;
  const trainingExamples = data?.trainingExamples?.items ?? [];
  const intelligence = data?.intelligenceDashboard;
  const intelligenceComponents = readObjectArray(intelligence?.components);
  const workerRoles = readObjectArray(intelligence?.worker_roles);
  const decisionTraces = readObjectArray(intelligence?.decision_traces);
  const intelligencePolicy = asObject(intelligence?.policy);
  const modelSummary = asObject(intelligence?.model_evaluation_summary);
  const policyRules = readObjectArray(intelligencePolicy.rules);
  const modelRows = readObjectArray(modelSummary.models);

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
              ["Legacy indexed files", data?.rag ? String(data.rag.indexed_file_count) : "Unavailable"],
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
        <PanelTitle icon={ShieldCheck} title="Intelligence Placement" />
        <div className="mini-metrics">
          <Metric label="Components" value={String(intelligenceComponents.length)} />
          <Metric label="Policy" value={readString(intelligencePolicy.version, "Unavailable")} />
          <Metric label="Worker roles" value={String(workerRoles.length)} />
          <Metric label="Decision traces" value={String(decisionTraces.length)} />
        </div>
        <div className="two-column">
          <div>
            <h3 className="section-subtitle">Components</h3>
            <div className="compact-list">
              {intelligenceComponents.slice(0, 8).map((component) => (
                <div key={readString(component.component_id)}>
                  <strong>{readString(component.component_id, "component")}</strong>
                  <span>{readString(component.role, "advisory")} / {readString(component.purpose)}</span>
                </div>
              ))}
              {!intelligenceComponents.length && <EmptyInline text="No intelligence registry returned." />}
            </div>
          </div>
          <div>
            <h3 className="section-subtitle">Policy</h3>
            <div className="compact-list">
              {policyRules.slice(0, 6).map((rule) => (
                <div key={readString(rule.rule_id)}>
                  <strong>{readString(rule.authority, "authority")}</strong>
                  <span>{readString(rule.statement)}</span>
                </div>
              ))}
              {!policyRules.length && <EmptyInline text="No model placement policy returned." />}
            </div>
          </div>
        </div>
        <div className="two-column">
          <div>
            <h3 className="section-subtitle">Worker roles</h3>
            <div className="compact-list">
              {workerRoles.slice(0, 6).map((role) => (
                <div key={readString(role.role_id)}>
                  <strong>{readString(role.role_id, "worker")}</strong>
                  <span>{readString(role.purpose)} / audit {readString(role.audit_event_type)}</span>
                </div>
              ))}
              {!workerRoles.length && <EmptyInline text="No worker role registry returned." />}
            </div>
          </div>
          <div>
            <h3 className="section-subtitle">Model evaluation summary</h3>
            <InfoList
              items={[
                ["Model statuses", formatDistribution(asNumberRecord(modelSummary.counts_by_status))],
                ["Fallback count", String(readNumber(modelSummary.fallback_count))],
                ["Low confidence count", String(readNumber(modelSummary.low_confidence_count))],
                ["Models authorize safety", readBoolean(asObject(intelligence?.auditability).models_authorize_safety) ? "Yes" : "No"],
              ]}
            />
            {modelRows.slice(0, 4).map((model) => (
              <div className="inline-record" key={readString(model.model_id)}>
                <strong>{readString(model.specialist, "model")}</strong>
                <span>{readString(model.status)} / {readString(model.used_for)}</span>
              </div>
            ))}
          </div>
        </div>
        <h3 className="section-subtitle">Recent decision traces</h3>
        <div className="compact-list">
          {decisionTraces.slice(0, 5).map((trace) => {
            const rag = asObject(trace.rag);
            const slm = asObject(trace.slm);
            return (
              <div key={readString(trace.trace_id)}>
                <strong>{readString(trace.selected_specialist, "Specialist")}</strong>
                <span>
                  RAG {readBoolean(rag.used) ? "used" : "skipped"} / SLM {readBoolean(slm.used) ? "used" : "skipped"} / safety {readString(trace.final_safety_status, "unknown")}
                </span>
              </div>
            );
          })}
          {!decisionTraces.length && <EmptyInline text="No recent decision traces yet." />}
        </div>
      </section>
      <section className="panel">
        <div className="panel-title-row">
          <PanelTitle icon={Database} title="Project RAG index" />
          <div className="button-row">
            <button className="secondary-button" onClick={onRebuildRagIndex} disabled={ragIndexLoading}>
              {ragIndexLoading ? <Activity size={16} className="spin" /> : <RefreshCw size={16} />}
              Rebuild project index
            </button>
            <button
              className="secondary-button"
              onClick={onRunRagEvaluation}
              disabled={ragEvaluationLoading}
            >
              {ragEvaluationLoading ? <Activity size={16} className="spin" /> : <CheckCircle2 size={16} />}
              Run RAG evaluation
            </button>
          </div>
        </div>
        {ragIndexNotice && (
          <Notice
            tone={ragIndexNotice.startsWith("Could not") ? "amber" : "blue"}
            text={ragIndexNotice}
          />
        )}
        {ragEvaluationNotice && (
          <Notice
            tone={ragEvaluationNotice.startsWith("Could not") || ragEvaluationNotice.includes("Build the project index") ? "amber" : "blue"}
            text={ragEvaluationNotice}
          />
        )}
        <InfoList
          items={[
            ["Index status", projectIndex?.exists ? "Ready" : projectIndex?.status ?? "Missing"],
            ["Root", projectIndex?.root ?? data?.rag?.project_root ?? "Unavailable"],
            ["Indexed files", String(projectIndex?.indexed_files ?? data?.rag?.project_index_file_count ?? 0)],
            ["Indexed chunks", String(projectIndex?.indexed_chunks ?? data?.rag?.project_index_chunk_count ?? 0)],
            ["Last indexed", projectIndex?.created_at ? formatDate(projectIndex.created_at) : "Not indexed yet"],
            ["Evaluation cases", String(data?.ragEvaluation?.evaluation_case_count ?? 0)],
            ["Latest path hit rate", evaluation ? `${Math.round(evaluation.path_hit_rate * 100)}%` : "Not run yet"],
            ["Latest pass rate", evaluation ? `${evaluation.passed_cases}/${evaluation.total_cases}` : "Not run yet"],
          ]}
        />
        {evaluation?.cases?.length ? (
          <div className="evaluation-list">
            {evaluation.cases.map((item) => (
              <details key={item.case_id} className={`evaluation-case ${item.passed ? "passed" : "failed"}`}>
                <summary>
                  <strong>{item.passed ? "Passed" : "Failed"}: {item.query}</strong>
                  <span>{item.category} / score {item.score}</span>
                </summary>
                <InfoList
                  items={[
                    ["Expected paths", item.expected_paths.join(", ") || "None"],
                    ["Returned paths", item.returned_paths.join(", ") || "None"],
                    ["Missing paths", item.missing_expected_paths.join(", ") || "None"],
                  ]}
                />
              </details>
            ))}
          </div>
        ) : (
          <EmptyInline text="No RAG evaluation has been run yet." />
        )}
      </section>
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
      <section className="panel">
        <div className="panel-title-row">
          <PanelTitle icon={Database} title="Training dataset" />
          <div className="button-row">
            <button
              className="secondary-button"
              onClick={() => onExportTrainingDataset("jsonl")}
              disabled={trainingActionLoading}
            >
              {trainingActionLoading ? <Activity size={16} className="spin" /> : <Database size={16} />}
              Export JSONL
            </button>
            <button
              className="secondary-button"
              onClick={() => onExportTrainingDataset("csv")}
              disabled={trainingActionLoading}
            >
              {trainingActionLoading ? <Activity size={16} className="spin" /> : <Database size={16} />}
              Export CSV
            </button>
          </div>
        </div>
        {trainingNotice && (
          <Notice
            tone={trainingNotice.startsWith("Could not") ? "amber" : "blue"}
            text={trainingNotice}
          />
        )}
        <InfoList
          items={[
            ["Total examples", String(trainingStatus?.total_examples ?? 0)],
            ["Labeled examples", String(trainingStatus?.labeled_count ?? 0)],
            ["Unlabeled examples", String(trainingStatus?.unlabeled_count ?? 0)],
            ["Label distribution", formatDistribution(trainingStatus?.label_distribution ?? {})],
            ["Last updated", trainingStatus?.last_updated ? formatDate(trainingStatus.last_updated) : "Not logged yet"],
            ["Storage path", trainingStatus?.storage_path ?? "Unavailable"],
          ]}
        />
        <h3 className="section-subtitle">Recent examples</h3>
        {trainingExamples.length ? (
          <div className="training-example-list">
            {trainingExamples.map((example) => (
              <TrainingExampleReview
                key={example.id}
                example={example}
                disabled={trainingActionLoading}
                onSubmit={onLabelTrainingExample}
              />
            ))}
          </div>
        ) : (
          <EmptyInline text="No training examples collected yet. Completed chats will appear here automatically." />
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
                          ["Grounding", groundingDisplay(selectedChatRun)] as [string, string],
                          ["Source count", String(selectedChatRun.source_count ?? ragSources(selectedChatRun).length)] as [string, string],
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
                              ["Grounding", groundingDisplay(turn)],
                              ["Source count", String(turn.source_count ?? ragSources(turn).length)],
                              ["Memory", turn.memory_used ? "used" : "not used"],
                              ["Safety/runtime", `${turn.safety_decision || "unknown"} / ${turn.runtime_decision || "unknown"}`],
                            ]}
                          />
                          {ragSources(turn).length > 0 && (
                            <div className="source-list">
                              <strong>RAG sources</strong>
                              {ragSources(turn).map((source) => (
                                <span key={`${source.path}-${source.startLine}-${source.endLine}`}>
                                  {formatSource(source)}
                                </span>
                              ))}
                            </div>
                          )}
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

const trainingLabels: TrainingLabel[] = [
  "general",
  "code",
  "rag",
  "runtime",
  "safety",
  "training",
  "frontend",
  "backend",
  "debugging",
  "testing",
  "unknown",
];

function TrainingExampleReview({
  example,
  disabled,
  onSubmit,
}: {
  example: TrainingExample;
  disabled: boolean;
  onSubmit: (exampleId: string, request: TrainingLabelRequest) => void;
}) {
  const [label, setLabel] = useState<TrainingLabel>(
    example.final_label ?? example.corrected_label ?? example.suggested_label ?? "unknown",
  );
  const [usefulness, setUsefulness] = useState<UsefulnessRating | "">(
    example.usefulness_rating ?? "",
  );
  const [notes, setNotes] = useState(example.notes ?? "");

  useEffect(() => {
    setLabel(example.final_label ?? example.corrected_label ?? example.suggested_label ?? "unknown");
    setUsefulness(example.usefulness_rating ?? "");
    setNotes(example.notes ?? "");
  }, [example]);

  const baseRequest = {
    usefulness_rating: usefulness || null,
    notes: notes.trim() || null,
  };

  return (
    <details className="training-example">
      <summary>
        <span>
          <strong>{example.suggested_label ?? "unknown"}</strong>
          <small>{example.label_status} / {example.source}</small>
        </span>
        <em>{example.user_message.slice(0, 120)}</em>
      </summary>
      <InfoList
        items={[
          ["Suggested label", example.suggested_label ?? "None"],
          ["Final label", example.final_label ?? "None"],
          ["Route", `${example.routed_specialist ?? "unknown"} / ${example.routed_task_type ?? "unknown"}`],
          ["RAG", example.rag_used ? `used / ${example.grounding_status ?? "unknown"}` : "not used"],
          ["Sources", example.source_paths.join(", ") || "None"],
          ["Created", formatDate(example.created_at)],
        ]}
      />
      <p>{example.user_message}</p>
      {example.assistant_response && <p className="training-response">{example.assistant_response}</p>}
      <div className="training-review-controls">
        <label className="field">
          <span>Correct label</span>
          <select value={label} onChange={(event) => setLabel(event.target.value as TrainingLabel)}>
            {trainingLabels.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Usefulness</span>
          <select
            value={usefulness}
            onChange={(event) => setUsefulness(event.target.value as UsefulnessRating | "")}
          >
            <option value="">Unrated</option>
            <option value="good">good</option>
            <option value="okay">okay</option>
            <option value="bad">bad</option>
          </select>
        </label>
        <label className="field training-notes">
          <span>Notes</span>
          <input
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Short review note"
          />
        </label>
      </div>
      <div className="button-row training-actions">
        <button
          className="secondary-button"
          disabled={disabled}
          onClick={() =>
            onSubmit(example.id, {
              ...baseRequest,
              label_status: "confirmed",
            })
          }
        >
          Confirm
        </button>
        <button
          className="secondary-button"
          disabled={disabled}
          onClick={() =>
            onSubmit(example.id, {
              ...baseRequest,
              corrected_label: label,
              label_status: "corrected",
            })
          }
        >
          Correct
        </button>
        <button
          className="danger-button"
          disabled={disabled}
          onClick={() =>
            onSubmit(example.id, {
              ...baseRequest,
              label_status: "rejected",
            })
          }
        >
          Reject
        </button>
      </div>
    </details>
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

function CreationResult({
  title,
  created,
  skipped,
  refused,
  warnings,
  nextSteps,
}: {
  title: string;
  created: string[];
  skipped: string[];
  refused: string[];
  warnings: string[];
  nextSteps: string[];
}) {
  return (
    <div className="creation-result">
      <h3 className="section-subtitle">{title}</h3>
      <InfoList
        items={[
          ["Created", created.join(", ") || "None"],
          ["Skipped", skipped.join(", ") || "None"],
          ["Refused", refused.join(", ") || "None"],
          ["Warnings", warnings.join(", ") || "None"],
          ["Next manual steps", nextSteps.join(" | ") || "Review generated files before running anything."],
        ]}
      />
    </div>
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

function LiveProgress({ steps }: { steps: ChatProgressStep[] }) {
  if (!steps.length) return null;
  return (
    <div className="live-progress">
      {steps.slice(-6).map((step) => (
        <div key={step.id} className={`live-step live-${step.status}`}>
          <CheckCircle2 size={14} />
          <span>
            <strong>{step.label}</strong>
            <small>{step.detail}</small>
          </span>
        </div>
      ))}
    </div>
  );
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
            latest_grounding_status: latest?.grounding_status ?? "none",
            latest_source_count: latest?.source_count ?? 0,
            latest_source_paths: latest?.source_paths ?? [],
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
              grounding_status: run.grounding_status ?? "none",
              source_count: run.source_count ?? 0,
              source_paths: run.source_paths ?? [],
              rag_sources: ragSources(run).map(formatSource),
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

function progressFromStreamEvent(event: ChatStreamEvent): ChatProgressStep | null {
  const id = `${event.event}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  if (event.event === "run_started") {
    return {
      id,
      label: "Run started",
      detail: readString(event.data.conversation_id, "Preparing conversation"),
      status: "active",
    };
  }
  if (event.event === "specialist_selected") {
    return {
      id,
      label: "Specialist selected",
      detail: `${readString(event.data.selected_specialist, "general_specialist")} / ${readString(event.data.intent, "unknown")}`,
      status: "done",
    };
  }
  if (event.event === "rag_completed") {
    const used = event.data.rag_used === true;
    return {
      id,
      label: "RAG completed",
      detail: used
        ? `Used ${readNumber(event.data.rag_context_count)} context item(s)`
        : `Skipped: ${readString(event.data.rag_skip_reason, "not needed")}`,
      status: used ? "done" : "warning",
    };
  }
  if (event.event === "safety_completed") {
    return {
      id,
      label: "Safety completed",
      detail: `${readString(event.data.safety_decision, "unknown")} / ${readString(event.data.runtime_decision, "unknown")}`,
      status: "done",
    };
  }
  if (event.event === "run_failed") {
    return {
      id,
      label: "Run failed",
      detail: readString(event.data.error, "Streaming failed"),
      status: "error",
    };
  }
  return null;
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

function ragSources(run: ChatRunResponse) {
  if (Array.isArray(run.rag_sources) && run.rag_sources.length > 0) {
    return run.rag_sources
      .map((source) => ({
        path: readString(source.path),
        startLine: readNumber(source.start_line, 0),
        endLine: readNumber(source.end_line, 0),
        score: readNumber(source.score, 0),
      }))
      .filter((source) => source.path);
  }
  const ragTrace = run.trace_summary?.find((entry) => entry.phase === "rag");
  const data = asObject(ragTrace?.data);
  const sources = Array.isArray(data.sources) ? data.sources : [];
  return sources
    .map((source) => {
      const raw = asObject(source);
      return {
        path: readString(raw.path),
        startLine: readNumber(raw.start_line, 0),
        endLine: readNumber(raw.end_line, 0),
        score: readNumber(raw.score, 0),
      };
    })
    .filter((source) => source.path);
}

function formatSource(source: { path: string; startLine: number; endLine: number; score?: number }) {
  const score = source.score && source.score > 0 ? ` · ${source.score.toFixed(2)}` : "";
  if (source.startLine > 0 && source.endLine > 0) {
    return `${source.path}:${source.startLine}-${source.endLine}${score}`;
  }
  return `${source.path}${score}`;
}

function formatDistribution(distribution: Record<string, number>) {
  const entries = Object.entries(distribution);
  if (!entries.length) return "None";
  return entries.map(([label, count]) => `${label}: ${count}`).join(", ");
}

function groundingDisplay(run: ChatRunResponse) {
  if (run.grounding_status === "grounded") return "grounded";
  if (run.grounding_status === "weak") return "weak";
  return "none";
}

function groundingTone(status: ChatRunResponse["grounding_status"]) {
  if (status === "grounded") return "green";
  if (status === "weak") return "amber";
  return "blue";
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

function assignmentFriendlyError(
  error: unknown,
  paths: { documentPath?: string; datasetPath?: string; workspacePath?: string } = {},
) {
  const raw = cleanError(error);
  const detail = extractErrorDetail(raw);
  const lowered = detail.toLowerCase();
  const candidatePath = paths.datasetPath || paths.documentPath || paths.workspacePath || "";
  const wslSuggestion = windowsPathToWsl(candidatePath);

  if (wslSuggestion) {
    return `Windows path detected. Try the WSL path instead: ${wslSuggestion}`;
  }
  if (lowered.includes("python-docx is required")) {
    return "python-docx is required to parse .docx files. Install python-docx or paste the assignment text.";
  }
  if (lowered.includes("must point to a file") || lowered.includes("path is a folder")) {
    return "That path points to a folder. For datasets, choose the actual .csv, .txt, or .tsv file. For assignments, choose the .txt, .md, or .docx document.";
  }
  if (lowered.includes("outside the allowed workspace root") || lowered.includes("workspace root")) {
    return "That file is outside Astra's allowed workspace. Copy it into assignment_inputs or assignment_workspaces, or use an allowed workspace path.";
  }
  if (lowered.includes("unsupported dataset") || lowered.includes("supported extensions: .csv")) {
    return "Unsupported dataset type. Use a .csv, .txt, or .tsv file.";
  }
  if (lowered.includes("not found")) {
    return `File not found. Check the path and, on WSL, use /mnt/c/... for Windows files. Details: ${detail}`;
  }
  return `Assignment Copilot could not continue: ${detail}`;
}

function extractErrorDetail(message: string) {
  try {
    const parsed = JSON.parse(message) as unknown;
    const detail = asObject(parsed).detail;
    return typeof detail === "string" ? detail : message;
  } catch {
    return message;
  }
}

function windowsPathToWsl(value: string) {
  const match = /^([a-zA-Z]):[\\/](.*)$/.exec(value.trim());
  if (!match) return "";
  return `/mnt/${match[1].toLowerCase()}/${match[2].replace(/\\/g, "/")}`;
}

function mappingColumn(value: Record<string, unknown>) {
  const column = readString(value.column, "Placeholder");
  const reason = readString(value.reason);
  return reason ? `${column} - ${reason}` : column;
}

function readString(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function readNumber(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function readObjectArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value
        .filter((item) => item && typeof item === "object" && !Array.isArray(item))
        .map((item) => item as Record<string, unknown>)
    : [];
}

function asNumberRecord(value: unknown): Record<string, number> {
  const object = asObject(value);
  return Object.fromEntries(
    Object.entries(object)
      .filter((entry): entry is [string, number] => typeof entry[1] === "number")
  );
}

function groupObjectsByString(
  items: Array<Record<string, unknown>>,
  key: string,
): Record<string, Array<Record<string, unknown>>> {
  return items.reduce<Record<string, Array<Record<string, unknown>>>>((groups, item) => {
    const group = readString(item[key], "General");
    groups[group] = [...(groups[group] ?? []), item];
    return groups;
  }, {});
}

function newId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default App;
