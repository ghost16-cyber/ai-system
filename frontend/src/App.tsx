import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleOff,
  CircleStop,
  Code2,
  Command,
  Cpu,
  Database,
  FileDiff,
  FileCode2,
  Folder,
  Gauge,
  HardDrive,
  Layers3,
  Menu,
  MemoryStick,
  MessageSquareText,
  MoreHorizontal,
  PanelLeftClose,
  Play,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  TestTube2,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { useState } from "react";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { SpecialistsView } from "./components/SpecialistsView";
import {
  deriveFeatureConnections,
  deriveRuntimeEvidence,
  mapJobToOrchestratorJob,
  mapHistoryToRunItem,
  mapToolToToolCall,
  useHealth,
  useHistory,
  useJobs,
  useRuntimeContext,
  useRuntimeResearchManifest,
  useTools,
} from "./api/hooks";
import { useAstraWorkflow } from "./hooks/useAstraWorkflow";
import type {
  AstraWorkflowState,
  ExecutionProfile,
  NavigationId,
  TaskKind,
} from "./types/contracts";

const navigation = [
  { id: "dashboard" as const, label: "Dashboard", icon: Gauge },
  { id: "workspace" as const, label: "Workspace", icon: Command },
  { id: "runtime" as const, label: "Runtime", icon: Cpu },
  { id: "specialists" as const, label: "Specialists", icon: Bot },
  { id: "profiles" as const, label: "Profiles", icon: Layers3 },
  { id: "traces" as const, label: "Traces", icon: Workflow },
  { id: "repository" as const, label: "Repository", icon: Folder },
  { id: "patches" as const, label: "Patches", icon: FileDiff },
  { id: "tests" as const, label: "Tests", icon: TestTube2 },
];

const headings: Record<NavigationId, [string, string]> = {
  dashboard: ["Astra overview", "Backend-connected product shell"],
  workspace: ["Task workspace", "Live orchestration via backend API"],
  runtime: ["Runtime intelligence", "Live hardware context and policy"],
  specialists: ["Specialists", "Backend specialist lifecycle and traces"],
  profiles: ["Execution profiles", "Validated plans compiled into settings"],
  traces: ["Trace audit", "Visible planning, policy, and tool decisions"],
  repository: ["Repository explorer", "File system access not yet available"],
  patches: ["Patch review", "Review only / apply actions disabled"],
  tests: ["Test results", "Run via orchestrated tasks"],
  settings: ["Preferences", "Browser-session settings only"],
};

const taskKinds: TaskKind[] = [
  "Code repair",
  "Local SLM",
  "RAG workflow",
  "Model training",
  "Classical ML",
];

function App() {
  const [activeNav, setActiveNav] = useState<NavigationId>("dashboard");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [taskKind, setTaskKind] = useState<TaskKind>("Local SLM");
  const [prompt, setPrompt] = useState(
    "Set up the best local coding model for this laptop and keep it responsive while I work.",
  );
  const [selectedRun, setSelectedRun] = useState(0);
  const [selectedPatch, setSelectedPatch] = useState(0);
  const [selectedPath, setSelectedPath] = useState(
    "backend/app/local_runtime/execution_profiles.py",
  );
  const workflow = useAstraWorkflow();

  // Real API data
  const { data: health } = useHealth();
  const { data: runtimeCtx } = useRuntimeContext();
  const { data: rawJobs } = useJobs(10_000);
  const { data: rawTools } = useTools();

  const featureConnections = deriveFeatureConnections(health, rawTools ?? []);
  const orchestratorJobs = (rawJobs ?? []).slice(0, 5).map(mapJobToOrchestratorJob);
  const toolCalls = (rawTools ?? []).map(mapToolToToolCall);

  const [heading, subtitle] = headings[activeNav];

  function navigate(id: NavigationId) {
    setActiveNav(id);
    setMobileNavOpen(false);
  }

  return (
    <div className="app-shell">
      <aside
        className={`sidebar ${sidebarOpen ? "" : "sidebar-collapsed"} ${
          mobileNavOpen ? "sidebar-mobile-open" : ""
        }`}
      >
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">
            <Sparkles size={18} strokeWidth={2.2} />
          </div>
          {sidebarOpen && (
            <div className="brand-copy">
              <strong>Astra</strong>
              <span>Local intelligence</span>
            </div>
          )}
          <button
            className="icon-button sidebar-close-mobile"
            aria-label="Close navigation"
            onClick={() => setMobileNavOpen(false)}
          >
            <X size={18} />
          </button>
        </div>

        <nav className="primary-nav" aria-label="Primary navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            const state = featureConnections.find(
              (feature) => feature.id === item.id,
            )?.state;
            return (
              <button
                key={item.id}
                className={`nav-item ${activeNav === item.id ? "active" : ""}`}
                onClick={() => navigate(item.id)}
                title={!sidebarOpen ? item.label : undefined}
              >
                <Icon size={18} strokeWidth={1.9} />
                {sidebarOpen && (
                  <>
                    <span>{item.label}</span>
                    {state && (
                      <span className={`nav-state-dot nav-state-${state}`} />
                    )}
                  </>
                )}
              </button>
            );
          })}
        </nav>

        <div className="sidebar-spacer" />
        {sidebarOpen && (
          <div className="machine-brief">
            <div className="machine-brief-top">
              <span className="status-dot" />
              <span>{health?.status === "ok" ? "Backend connected" : "Connecting…"}</span>
            </div>
            <strong>{runtimeCtx?.machine.gpu ?? "—"}</strong>
            <span>
              {runtimeCtx
                ? `${runtimeCtx.machine.vramGb} GB VRAM / ${runtimeCtx.machine.ramGb} GB RAM`
                : "Loading hardware…"}
            </span>
          </div>
        )}
        <button
          className={`nav-item ${activeNav === "settings" ? "active" : ""}`}
          onClick={() => navigate("settings")}
          title={!sidebarOpen ? "Settings" : undefined}
        >
          <Settings size={18} strokeWidth={1.9} />
          {sidebarOpen && (
            <>
              <span>Settings</span>
              <span className="nav-state-dot nav-state-mock" />
            </>
          )}
        </button>
      </aside>

      {mobileNavOpen && (
        <button
          className="mobile-scrim"
          aria-label="Close navigation"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <div className="main-column">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="icon-button desktop-sidebar-toggle"
              aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
              onClick={() => setSidebarOpen((value) => !value)}
            >
              <PanelLeftClose
                size={19}
                className={sidebarOpen ? "" : "flip-horizontal"}
              />
            </button>
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobileNavOpen(true)}
            >
              <Menu size={20} />
            </button>
            <div>
              <h1>{heading}</h1>
              <p>{subtitle}</p>
            </div>
          </div>
          <div className="topbar-actions">
            <ConnectionBadge state={health?.status === "ok" ? "connected" : "disabled"} />
            <button className="search-button">
              <Search size={17} />
              <span>Search</span>
              <kbd>Ctrl K</kbd>
            </button>
            <button className="icon-button" aria-label="More options">
              <MoreHorizontal size={19} />
            </button>
            <div className="avatar" title="Local user">
              P
            </div>
          </div>
        </header>

        <main className="content">
          {activeNav === "dashboard" && (
            <DashboardView
              onNavigate={navigate}
              workflowState={workflow.state}
              orchestratorJobs={orchestratorJobs}
              featureConnections={featureConnections}
              jobCount={rawJobs?.length ?? 0}
              historyCount={0}
            />
          )}
          {activeNav === "workspace" && (
            <WorkspaceView
              prompt={prompt}
              setPrompt={setPrompt}
              taskKind={taskKind}
              setTaskKind={setTaskKind}
              workflowState={workflow.state}
              submitting={workflow.submitting}
              startRun={() => void workflow.submit(prompt, taskKind)}
              resetRun={workflow.reset}
              selectedRun={selectedRun}
              setSelectedRun={setSelectedRun}
              onNavigate={navigate}
              onOpenProfile={() => navigate("profiles")}
            />
          )}
          {activeNav === "runtime" && (
            <RuntimeView toolCalls={toolCalls} />
          )}
          {activeNav === "specialists" && <SpecialistsView />}
          {activeNav === "profiles" && (
            <ProfilesView activeProfile={workflow.state.activeProfile} />
          )}
          {activeNav === "traces" && (
            <TracesView workflowState={workflow.state} />
          )}
          {activeNav === "repository" && (
            <RepositoryView
              selectedPath={selectedPath}
              setSelectedPath={setSelectedPath}
            />
          )}
          {activeNav === "patches" && (
            <PatchesView
              selected={selectedPatch}
              setSelected={setSelectedPatch}
              workflowState={workflow.state}
            />
          )}
          {activeNav === "tests" && (
            <TestsView workflowState={workflow.state} />
          )}
          {activeNav === "settings" && <SettingsView />}
        </main>
      </div>
    </div>
  );
}

function DashboardView({
  onNavigate,
  workflowState,
  orchestratorJobs,
  featureConnections,
  jobCount,
  historyCount,
}: {
  onNavigate: (id: NavigationId) => void;
  workflowState: AstraWorkflowState;
  orchestratorJobs: ReturnType<typeof mapJobToOrchestratorJob>[];
  featureConnections: ReturnType<typeof deriveFeatureConnections>;
  jobCount: number;
  historyCount: number;
}) {
  return (
    <div className="page-stack">
      <section className="dashboard-strip">
        <div>
          <span className="eyebrow">AI System</span>
          <h2>Astra control center</h2>
          <p>
            Research-backed runtime decisions. Live backend integration active.
          </p>
        </div>
        <button className="primary-button" onClick={() => onNavigate("workspace")}>
          <Plus size={16} /> New task
        </button>
      </section>

      <div className="stats-grid">
        <StatBlock
          icon={Activity}
          label="Jobs"
          value={String(jobCount)}
          sub={jobCount === 0 ? "No jobs yet" : `${jobCount} total`}
        />
        <StatBlock
          icon={ShieldCheck}
          label="Runtime policy"
          value="Live"
          sub="Deterministic gate active"
        />
        <StatBlock
          icon={FileDiff}
          label="Analyses"
          value={String(historyCount)}
          sub={historyCount === 0 ? "No analyses yet" : `${historyCount} recorded`}
        />
        <StatBlock
          icon={TestTube2}
          label="Backend"
          value={featureConnections[0]?.state === "connected" ? "Connected" : "Offline"}
          sub="Live API"
        />
      </div>

      <div className="dashboard-grid">
        <section className="data-section">
          <SectionHeading eyebrow="Product status" title="Connection states" />
          <div className="feature-state-list">
            {featureConnections.map((feature) => (
              <button
                key={feature.id}
                className="feature-state-row"
                onClick={() => {
                  if (feature.id !== "toolchain") onNavigate(feature.id);
                }}
              >
                <div>
                  <strong>{feature.label}</strong>
                  <span>{feature.detail}</span>
                </div>
                <ConnectionBadge state={feature.state} />
              </button>
            ))}
          </div>
        </section>

        <section className="data-section">
          <SectionHeading eyebrow="Orchestration" title="Latest jobs" />
          {workflowState.stage !== "idle" && (
            <div className="live-workflow-row">
              <Activity
                size={16}
                className={
                  workflowState.stage === "blocked" ? "" : "spin-soft"
                }
              />
              <div>
                <strong>{workflowState.task}</strong>
                <span>
                  {workflowState.stage.replace(/_/g, " ")} /{" "}
                  {workflowState.decision ?? "pending"}
                </span>
              </div>
              {workflowState.decision && (
                <DecisionBadge decision={workflowState.decision} />
              )}
            </div>
          )}
          {orchestratorJobs.length === 0 ? (
            <div className="empty-run">
              <div className="empty-run-icon"><Activity size={18} /></div>
              <div>
                <strong>No jobs yet</strong>
                <span>Submit a task from the Workspace to create your first job.</span>
              </div>
            </div>
          ) : (
            <div className="job-list">
              {orchestratorJobs.map((job) => (
                <div className="job-row" key={job.id}>
                  <span className={`decision-icon decision-${job.decision}`}>
                    {job.decision === "allow" ? (
                      <Check size={14} />
                    ) : job.decision === "downgrade" ? (
                      <AlertTriangle size={14} />
                    ) : (
                      <CircleStop size={14} />
                    )}
                  </span>
                  <div>
                    <strong>{job.title}</strong>
                    <span>
                      {job.taskType} / {job.duration}
                    </span>
                  </div>
                  <DecisionBadge decision={job.decision} />
                  <time>{job.updatedAt}</time>
                </div>
              ))}
            </div>
          )}
          <button className="rail-link" onClick={() => onNavigate("traces")}>
            Inspect trace audit <ChevronRight size={15} />
          </button>
        </section>
      </div>
    </div>
  );
}

type WorkspaceProps = {
  prompt: string;
  setPrompt: (value: string) => void;
  taskKind: TaskKind;
  setTaskKind: (value: TaskKind) => void;
  workflowState: AstraWorkflowState;
  submitting: boolean;
  startRun: () => void;
  resetRun: () => void;
  selectedRun: number;
  setSelectedRun: (value: number) => void;
  onNavigate: (id: NavigationId) => void;
  onOpenProfile: () => void;
};

function WorkspaceView({
  prompt,
  setPrompt,
  taskKind,
  setTaskKind,
  workflowState,
  submitting,
  startRun,
  resetRun,
  selectedRun,
  setSelectedRun,
  onNavigate,
  onOpenProfile,
}: WorkspaceProps) {
  const { data: runtimeCtx } = useRuntimeContext();
  const { data: rawHistory } = useHistory(5);
  const recentRuns = (rawHistory ?? []).map(mapHistoryToRunItem);
  const busy = submitting || isWorkflowRunning(workflowState);
  return (
    <div className="workspace-grid">
      <section className="workspace-main">
        <div className="section-heading">
          <div>
            <span className="eyebrow">New task</span>
            <h2>What should Astra work on?</h2>
          </div>
          <ConnectionBadge state="connected" />
        </div>

        <div className="composer">
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Describe the outcome you want..."
            aria-label="Task description"
          />
          <div className="composer-footer">
            <div className="task-type-control">
              <Code2 size={16} />
              <select
                value={taskKind}
                onChange={(event) => setTaskKind(event.target.value as TaskKind)}
                aria-label="Task type"
              >
                {taskKinds.map((kind) => (
                  <option key={kind}>{kind}</option>
                ))}
              </select>
              <ChevronDown size={14} />
            </div>
            <div className="composer-actions">
              <button
                className="icon-button"
                aria-label="Select repository files"
                title="Select repository files"
                onClick={() => onNavigate("repository")}
              >
                <Plus size={18} />
              </button>
              <button
                className="primary-button"
                onClick={busy ? undefined : startRun}
                disabled={busy || !prompt.trim()}
              >
                {submitting ? (
                  <>
                    <Activity size={17} className="spin-soft" /> Fetching plan…
                  </>
                ) : isWorkflowRunning(workflowState) ? (
                  <>
                    <Activity size={17} className="spin-soft" /> Running
                  </>
                ) : (
                  <>
                    <Play size={16} fill="currentColor" /> Run task
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        <div className="suggestion-row">
          <Suggestion
            icon={TestTube2}
            label="Repair failing tests"
            onClick={() => {
              setTaskKind("Code repair");
              setPrompt(
                "Inspect this project, run the tests, and repair the smallest safe issue.",
              );
            }}
          />
          <Suggestion
            icon={Database}
            label="Plan a RAG index"
            onClick={() => {
              setTaskKind("RAG workflow");
              setPrompt(
                "Create a local retrieval plan for this repository and keep the context compact.",
              );
            }}
          />
          <Suggestion
            icon={Gauge}
            label="Tune training settings"
            onClick={() => {
              setTaskKind("Model training");
              setPrompt(
                "Choose safe PyTorch settings for a small image classifier on this laptop.",
              );
            }}
          />
          <Suggestion
            icon={Cpu}
            label="Classical ML baseline"
            onClick={() => {
              setTaskKind("Classical ML");
              setPrompt(
                "Build a lightweight scikit-learn baseline for tabular data without using the GPU.",
              );
            }}
          />
        </div>

        <RunPanel
          workflowState={workflowState}
          resetRun={resetRun}
          onOpenProfile={() => onOpenProfile()}
        />

        <section className="recent-section">
          <div className="section-heading compact">
            <div>
              <span className="eyebrow">Activity</span>
              <h2>Recent runs</h2>
            </div>
            <button className="text-button" onClick={() => onNavigate("traces")}>
              View traces <ArrowRight size={15} />
            </button>
          </div>
          <div className="run-list">
            {recentRuns.map((run, index) => (
              <button
                className={`run-row ${selectedRun === index ? "selected" : ""}`}
                key={run.id}
                onClick={() => setSelectedRun(index)}
              >
                <div className={`run-icon ${run.accent}`}>
                  {run.type === "Code repair" ? (
                    <FileCode2 size={18} />
                  ) : run.type === "RAG workflow" ? (
                    <Database size={18} />
                  ) : (
                    <Bot size={18} />
                  )}
                </div>
                <div className="run-copy">
                  <strong>{run.title}</strong>
                  <span>
                    {run.type} / {run.meta}
                  </span>
                </div>
                <span className={`status-label ${run.accent}`}>{run.status}</span>
                <time>{run.time}</time>
                <ChevronRight size={17} className="row-chevron" />
              </button>
            ))}
          </div>
        </section>
      </section>

      <aside className="workspace-rail">
        <RuntimeSummary context={runtimeCtx} />
        <ProfileSummary
          profile={workflowState.activeProfile ?? null}
          preview={workflowState.stage === "idle"}
        />
      </aside>
    </div>
  );
}

function Suggestion({
  icon: Icon,
  label,
  onClick,
}: {
  icon: typeof Gauge;
  label: string;
  onClick: () => void;
}) {
  return (
    <button onClick={onClick}>
      <Icon size={15} /> {label}
    </button>
  );
}

function RunPanel({
  workflowState,
  resetRun,
  onOpenProfile,
}: {
  workflowState: AstraWorkflowState;
  resetRun: () => void;
  onOpenProfile: () => void;
}) {
  if (workflowState.stage === "idle") {
    return (
      <div className="empty-run">
        <div className="empty-run-icon">
          <MessageSquareText size={21} />
        </div>
        <div>
          <strong>Your task plan will appear here</strong>
          <span>
            Submit a task to kick off live runtime validation, profile compilation,
            and backend orchestration.
          </span>
        </div>
      </div>
    );
  }

  return (
    <section className="active-run">
      <div className="active-run-header">
        <div>
          <span className="eyebrow">
            {isWorkflowRunning(workflowState)
              ? "Orchestrating"
              : workflowState.stage === "blocked"
                ? "Plan blocked"
                : workflowState.stage === "failed"
                  ? "Workflow failed"
                  : "Workflow complete"}
          </span>
          <h2>{workflowState.task}</h2>
        </div>
        <div className="run-header-actions">
          <span className={`run-state ${workflowState.stage}`}>
            {isWorkflowRunning(workflowState) ? (
              <>
                <span className="pulse-dot" />{" "}
                {workflowState.stage.replace(/_/g, " ")}
              </>
            ) : workflowState.stage === "blocked" ? (
              <>
                <CircleStop size={14} /> Blocked
              </>
            ) : workflowState.stage === "failed" ? (
              <>
                <AlertTriangle size={14} /> Failed
              </>
            ) : (
              <>
                <Check size={14} /> Ready
              </>
            )}
          </span>
          {!isWorkflowRunning(workflowState) && (
            <button
              className="icon-button"
              aria-label="Close task plan"
              onClick={resetRun}
            >
              <X size={17} />
            </button>
          )}
        </div>
      </div>
      <div className="timeline">
        {workflowState.traceEvents.map((step) => {
          const active = step.status === "active";
          return (
            <div
              className={`timeline-step visible step-${step.status} ${
                active ? "active" : ""
              }`}
              key={step.id}
            >
              <div className="timeline-marker">
                {step.status === "passed" ? (
                  <Check size={14} strokeWidth={2.5} />
                ) : step.status === "blocked" ? (
                  <CircleStop size={14} />
                ) : active ? (
                  <Activity size={14} />
                ) : (
                  <Workflow size={14} />
                )}
              </div>
              <div className="timeline-copy">
                <strong>{step.title}</strong>
                <span>{step.detail}</span>
              </div>
              <span className="step-time">{step.elapsed}</span>
            </div>
          );
        })}
      </div>
      {(workflowState.runtimeEvidence.length > 0 ||
        workflowState.policyExplanations.length > 0) && (
        <div className="run-evidence-grid">
          <div>
            <span className="eyebrow">Research evidence</span>
            {workflowState.runtimeEvidence.map((item) => (
              <EvidenceRow key={item.id} label={item.label} value={item.value}>
                {item.detail}
              </EvidenceRow>
            ))}
          </div>
          <div>
            <span className="eyebrow">Policy explanation</span>
            {workflowState.policyExplanations.map((item) => (
              <PolicyRow
                key={item.id}
                title={item.title}
                value={item.detail}
                tone={item.tone}
              />
            ))}
          </div>
        </div>
      )}
      {(workflowState.slmSignal || workflowState.specialistSignals.length > 0) && (
        <div className="ai-stack-panel">
          {workflowState.slmSignal && (
            <SignalRow
              label="SLM coordinator"
              value={workflowState.slmSignal.proposedAction}
              detail={workflowState.slmSignal.reason}
            />
          )}
          {workflowState.specialistSignals.map((signal) => (
            <SignalRow
              key={`${signal.specialist}-${signal.label}`}
              label={signal.specialist.replace(/_/g, " ")}
              value={`${signal.label} / ${Math.round(signal.confidence * 100)}%`}
              detail={signal.reason}
            />
          ))}
          {workflowState.validation && (
            <SignalRow
              label="Deterministic policy"
              value={workflowState.validation.decision}
              detail={workflowState.validation.reason}
            />
          )}
          {workflowState.decision && (
            <SignalRow
              label="Final state"
              value={workflowState.decision}
              detail="Specialist and SLM signals are advisory; policy remains authoritative."
            />
          )}
        </div>
      )}
      {workflowState.finalMessage && (
        <div
          className={`plan-result ${
            workflowState.stage === "blocked" ? "plan-result-blocked" : ""
          }`}
        >
          <div>
            {workflowState.stage === "blocked" ? (
              <CircleStop size={19} />
            ) : (
              <ShieldCheck size={19} />
            )}
            <span>
              <strong>
                {workflowState.stage === "blocked"
                  ? "Runtime policy stopped this plan"
                  : "Workflow ready"}
              </strong>
              {workflowState.finalMessage}
            </span>
          </div>
          {workflowState.stage === "completed" && (
            <button className="secondary-button" onClick={onOpenProfile}>
              Open profile <ArrowRight size={15} />
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function RuntimeSummary({ context }: { context: import("./types/contracts").RuntimeContext | null }) {
  return (
    <section className="rail-section">
      <div className="rail-heading">
        <div>
          <span className="eyebrow">Runtime</span>
          <h3>This machine</h3>
        </div>
        <ConnectionBadge state={context ? "connected" : "disabled"} compact />
      </div>
      {context ? (
        <>
          <div className="hardware-name">
            <div className="hardware-icon">
              <Cpu size={20} />
            </div>
            <div>
              <strong>{context.machine.gpu || "CPU only"}</strong>
              <span>{context.machine.cudaAvailable ? "CUDA available" : "No CUDA"}</span>
            </div>
          </div>
          <MetricRow
            icon={MemoryStick}
            label="VRAM"
            value={`${context.machine.vramGb} GB`}
            detail={context.policy.lowVramMode ? "Low-VRAM mode" : "Normal mode"}
            percent={context.machine.vramGb < 8 ? 76 : 40}
            tone={context.machine.vramGb < 8 ? "amber" : "green"}
          />
          <MetricRow
            icon={Cpu}
            label="System RAM"
            value={`${context.machine.ramGb} GB`}
            detail={`${context.machine.logicalCores} cores`}
            percent={37}
            tone="green"
          />
          <MetricRow
            icon={HardDrive}
            label="Storage"
            value={`${context.machine.storageFreeGb} GB free`}
            detail="Workspace storage"
            percent={64}
            tone="blue"
          />
        </>
      ) : (
        <div className="profile-empty">
          <Activity size={16} className="spin-soft" />
          <span>Loading hardware context…</span>
        </div>
      )}
    </section>
  );
}

function MetricRow({
  icon: Icon,
  label,
  value,
  detail,
  percent,
  tone,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  detail: string;
  percent: number;
  tone: string;
}) {
  return (
    <div className="metric-row">
      <div className="metric-label">
        <Icon size={16} />
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <div className="meter">
        <span className={tone} style={{ width: `${percent}%` }} />
      </div>
      <span className="metric-detail">{detail}</span>
    </div>
  );
}

function ProfileSummary({
  profile,
  preview = false,
}: {
  profile: ExecutionProfile | null;
  preview?: boolean;
}) {
  if (profile === null) {
    return (
      <section className="rail-section execution-card">
        <div className="rail-heading">
          <div>
            <span className="eyebrow">Execution profile</span>
            <h3>No active profile</h3>
          </div>
          <ConnectionBadge state="disabled" compact />
        </div>
        <div className="profile-empty">
          <CircleStop size={20} />
          <span>
            Runtime policy stopped the plan before profile compilation.
          </span>
        </div>
      </section>
    );
  }
  return (
    <section className="rail-section execution-card">
      <div className="rail-heading">
        <div>
          <span className="eyebrow">
            {preview ? "Preview profile" : "Active profile"}
          </span>
          <h3>{profile.name}</h3>
        </div>
        <span className={`profile-status profile-${profile.status}`}>
          <ShieldCheck size={14} /> {profile.status}
        </span>
      </div>
      <dl className="profile-list">
        <ProfileItem term="Runtime" value={profile.runtime} />
        <ProfileItem term="Device" value={profile.device.toUpperCase()} />
        {profile.settings.slice(0, 4).map((setting) => (
          <ProfileItem
            key={setting.label}
            term={setting.label}
            value={setting.value}
          />
        ))}
      </dl>
      <div className="profile-note">
        <AlertTriangle size={16} />
        <span>{profile.safeguards[0]}</span>
      </div>
    </section>
  );
}

function RuntimeView({ toolCalls }: { toolCalls: ReturnType<typeof mapToolToToolCall>[] }) {
  const { data: ctx } = useRuntimeContext();
  const { data: manifest } = useRuntimeResearchManifest();
  const runtimeEvidence = ctx ? deriveRuntimeEvidence(ctx) : [];
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Machine intelligence"
        title="Hardware, research, policy, and toolchain"
        detail="Live hardware context and research evidence from backend probes."
        state={ctx ? "connected" : "disabled"}
      />
      <div className="stats-grid">
        <StatBlock
          icon={Cpu}
          label="CPU"
          value={ctx?.machine.cpu ?? "—"}
          sub={ctx ? `${ctx.machine.logicalCores} logical cores` : "Loading…"}
        />
        <StatBlock
          icon={Zap}
          label="GPU"
          value={ctx?.machine.gpu || "None"}
          sub={ctx ? (ctx.machine.cudaAvailable ? `CUDA / ${ctx.machine.vramGb} GB VRAM` : "No CUDA") : "Loading…"}
        />
        <StatBlock
          icon={MemoryStick}
          label="Memory"
          value={ctx ? `${ctx.machine.ramGb} GB` : "—"}
          sub="System RAM"
        />
        <StatBlock
          icon={HardDrive}
          label="Workspace"
          value={ctx ? `${ctx.machine.storageFreeGb} GB free` : "—"}
          sub="Available storage"
        />
      </div>
      <div className="runtime-layout">
        <section className="data-section">
          <SectionHeading eyebrow="Policy" title="Runtime decisions" />
          {ctx ? (
            <div className="policy-list">
              <PolicyRow title="Low-VRAM mode" value={ctx.policy.lowVramMode ? "Enabled" : "Disabled"} tone={ctx.policy.lowVramMode ? "amber" : "green"} />
              <PolicyRow title="Quantized models" value={ctx.policy.preferQuantizedModels ? "Preferred" : "Optional"} tone={ctx.policy.preferQuantizedModels ? "green" : "blue"} />
              <PolicyRow title="Large local models" value={ctx.policy.avoidLargeModels ? "Restricted" : "Allowed"} tone={ctx.policy.avoidLargeModels ? "red" : "green"} />
              <PolicyRow title="CPU fallback" value={ctx.policy.cpuFallbackAllowed ? "Allowed" : "Disabled"} tone={ctx.policy.cpuFallbackAllowed ? "green" : "amber"} />
              <PolicyRow title="RAG before fine-tuning" value={ctx.policy.preferRagOverFinetuning ? "Preferred" : "Optional"} tone={ctx.policy.preferRagOverFinetuning ? "blue" : "green"} />
            </div>
          ) : (
            <div className="profile-empty"><Activity size={16} className="spin-soft" /><span>Loading policy…</span></div>
          )}
        </section>
        <section className="data-section">
          <SectionHeading eyebrow="Toolchain" title="Registered tools" />
          {toolCalls.length === 0 ? (
            <div className="profile-empty"><Activity size={16} className="spin-soft" /><span>Loading tools…</span></div>
          ) : (
            <div className="tool-compact-list">
              {toolCalls.map((tool) => (
                <div className="tool-compact-row" key={tool.id}>
                  <div>
                    <TerminalSquare size={16} />
                    <span>
                      <strong>{tool.name}</strong>
                      <small>{tool.detail}</small>
                    </span>
                  </div>
                  <ConnectionBadge state={tool.state} />
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      {(runtimeEvidence.length > 0 || manifest) && (
        <div className="runtime-audit-grid">
          <section className="data-section">
            <SectionHeading eyebrow="Research baseline" title="Evidence applied" />
            {manifest && (
              <div className="manifest-card">
                <strong>{manifest.hardwareBaseline.gpu || "CPU"}</strong>
                <span>
                  {manifest.hardwareBaseline.vramGb} GB VRAM / CUDA{" "}
                  {manifest.hardwareBaseline.cudaAvailable ? "available" : "unavailable"}{" "}
                  / {manifest.sourceFolder}
                </span>
                <small>{manifest.usageNote}</small>
              </div>
            )}
            <div className="evidence-list">
              {runtimeEvidence.map((item) => (
                <EvidenceRow key={item.id} label={item.label} value={item.value}>
                  {item.detail}
                </EvidenceRow>
              ))}
            </div>
          </section>
          <section className="data-section">
            <SectionHeading eyebrow="AI architecture" title="Coordinator, specialists, policy" />
            <div className="ai-stack-panel ai-stack-panel-flat">
              <SignalRow label="SLM coordinator" value="understand / plan / explain" detail="The SLM proposes actions and explanations but does not execute tools." />
              <SignalRow label="Specialist models" value="predict / classify / rank" detail="Intent and error classifiers are advisory signals." />
              <SignalRow label="Deterministic backend" value="approve / block / verify" detail="Runtime gates, patch validation, and tool policy remain the final authority." />
              <SignalRow label="Frontend" value="review / approve" detail="The control center displays decisions while dangerous actions stay disabled." />
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function ProfilesView({ activeProfile }: { activeProfile: ExecutionProfile | null }) {
  if (!activeProfile) {
    return (
      <div className="page-stack">
        <PageIntro
          eyebrow="Execution profiles"
          title="No active profile"
          detail="Run a task from the Workspace view to build a live execution profile."
          state="disabled"
        />
        <div className="data-section">
          <div className="empty-run">
            <div className="empty-run-icon"><Layers3 size={21} /></div>
            <div>
              <strong>Profile will appear here after a task runs</strong>
              <span>The backend compiles an execution profile from your runtime context and validated plan.</span>
            </div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="split-view">
      <section className="data-section browser-pane">
        <SectionHeading eyebrow="Active profile" title="Current run" />
        <div className="profile-browser">
          <button className="profile-browser-row selected">
            <span className={`run-icon profile-${activeProfile.status}`}>
              <Layers3 size={17} />
            </span>
            <span>
              <strong>{activeProfile.name}</strong>
              <small>{activeProfile.runtime} / {activeProfile.device}</small>
            </span>
            <span className={`status-label profile-${activeProfile.status}`}>{activeProfile.status}</span>
          </button>
        </div>
      </section>
      <section className="data-section detail-pane">
        <div className="detail-title-row">
          <div>
            <span className="eyebrow">{activeProfile.taskType}</span>
            <h2>{activeProfile.name}</h2>
            <p>{activeProfile.strategy}</p>
          </div>
          <ConnectionBadge state="connected" />
        </div>
        <div className="profile-overview-grid">
          <ProfileItem term="Runtime" value={activeProfile.runtime} />
          <ProfileItem term="Device" value={activeProfile.device.toUpperCase()} />
          <ProfileItem term="Status" value={activeProfile.status} />
          <ProfileItem term="Source" value="Validated active plan" />
        </div>
        <h3 className="subsection-title">Execution settings</h3>
        <div className="settings-table">
          {activeProfile.settings.map((setting) => (
            <div key={setting.label}>
              <span>{setting.label}</span>
              <strong>{setting.value}</strong>
            </div>
          ))}
        </div>
        <h3 className="subsection-title">Safeguards</h3>
        <ul className="safeguard-list">
          {activeProfile.safeguards.map((item) => (
            <li key={item}>
              <ShieldCheck size={15} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <button className="primary-button" disabled>
          <CircleOff size={15} /> Approval unavailable
        </button>
      </section>
    </div>
  );
}

function TracesView({
  workflowState,
}: {
  workflowState: AstraWorkflowState;
}) {
  const events = workflowState.traceEvents;
  const decision = workflowState.decision ?? "allow";
  const requestedPlan = summarizePlan(
    workflowState.validation?.requestedPlan,
    "—",
  );
  const activePlan =
    decision === "block"
      ? "blocked before execution"
      : summarizePlan(
          workflowState.validation?.recommendedPlan,
          "—",
        );
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Auditability"
        title="Runtime and orchestration trace"
        detail="Live planning, policy, and tool decisions from workflow runs."
        state={workflowState.stage !== "idle" ? "connected" : "disabled"}
      />
      <section className="trace-layout">
        <div className="trace-summary">
          <DecisionBadge decision={decision} />
          <div>
            <strong>{workflowState.task || "No task run yet"}</strong>
            <span>
              {workflowState.runId ?? "—"} /{" "}
              {workflowState.stage === "idle"
                ? "run a task to see traces"
                : workflowState.stage.replace(/_/g, " ")}
            </span>
          </div>
          <span className="trace-summary-spacer" />
          <ProfileItem term="Requested" value={requestedPlan} />
          <ProfileItem term="Active plan" value={activePlan} />
        </div>
        {events.length === 0 ? (
          <div className="empty-run">
            <div className="empty-run-icon"><Workflow size={21} /></div>
            <div>
              <strong>No trace events yet</strong>
              <span>Submit a task from the Workspace to see live trace events here.</span>
            </div>
          </div>
        ) : (
          <div className="trace-event-list">
            {events.map((event, index) => (
              <div className="trace-event" key={event.id}>
                <div className={`trace-node trace-${event.status}`}>
                  {event.status === "passed" ? (
                    <Check size={14} />
                  ) : event.status === "blocked" ? (
                    <CircleStop size={14} />
                  ) : (
                    <AlertTriangle size={14} />
                  )}
                </div>
                <div>
                  <span className="trace-phase">
                    {index + 1}. {event.phase}
                  </span>
                  <strong>{event.title}</strong>
                  <p>{event.detail}</p>
                </div>
                <time>{event.elapsed}</time>
              </div>
            ))}
          </div>
        )}
        {workflowState.stage !== "idle" && (
          <div className="trace-ai-stack">
            {workflowState.slmSignal && (
              <SignalRow
                label="SLM coordinator"
                value={workflowState.slmSignal.proposedAction}
                detail={workflowState.slmSignal.reason}
              />
            )}
            {workflowState.specialistSignals.map((signal) => (
              <SignalRow
                key={`${signal.specialist}-${signal.label}`}
                label={signal.specialist.replace(/_/g, " ")}
                value={`${signal.label} / ${Math.round(signal.confidence * 100)}%`}
                detail={signal.reason}
              />
            ))}
            {workflowState.validation && (
              <SignalRow
                label="Deterministic policy"
                value={workflowState.validation.decision}
                detail={workflowState.validation.reason}
              />
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function RepositoryView({
  selectedPath,
  setSelectedPath,
}: {
  selectedPath: string;
  setSelectedPath: (path: string) => void;
}) {
  void selectedPath;
  void setSelectedPath;

  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Repository"
        title="File system access not available"
        detail="A backend workspace file API is required to browse and read files."
        state="disabled"
      />
      <div className="data-section">
        <div className="empty-run">
          <div className="empty-run-icon"><Folder size={21} /></div>
          <div>
            <strong>No file access API connected</strong>
            <span>Repository browsing will be available once a workspace file contract is implemented in the backend.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function PatchesView({
  selected,
  setSelected,
  workflowState,
}: {
  selected: number;
  setSelected: (index: number) => void;
  workflowState: AstraWorkflowState;
}) {
  void selected;
  void setSelected;

  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Patch review"
        title="No patches in queue"
        detail="Patches are generated from code analysis runs. Use Analyze File or Analyze Project to produce proposals."
        state={workflowState.patchVisible ? "connected" : "disabled"}
      />
      <div className="data-section">
        {workflowState.patchVisible ? (
          <div className="empty-run">
            <div className="empty-run-icon"><FileDiff size={21} /></div>
            <div>
              <strong>Patch generated by workflow</strong>
              <span>Patch review UI requires a full patch proposal from the analysis API. Apply remains disabled.</span>
            </div>
          </div>
        ) : (
          <div className="empty-run">
            <div className="empty-run-icon"><FileDiff size={21} /></div>
            <div>
              <strong>No patch proposals yet</strong>
              <span>Run a Code Repair task or use the /analyze-file endpoint to produce patch proposals.</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TestsView({
  workflowState,
}: {
  workflowState: AstraWorkflowState;
}) {
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Verification"
        title="Test results"
        detail="Tests run automatically during orchestrated tasks with allow_tests enabled."
        state={workflowState.testsVisible ? "connected" : "disabled"}
      />
      {workflowState.testsRunning ? (
        <div className="data-section">
          <div className="empty-run">
            <div className="empty-run-icon"><Activity size={21} className="spin-soft" /></div>
            <div>
              <strong>Tests running…</strong>
              <span>Waiting for verification results from the backend.</span>
            </div>
          </div>
        </div>
      ) : workflowState.testsVisible ? (
        <div className="data-section">
          <div className="suite-row">
            <span className="decision-icon decision-allow"><Check size={14} /></span>
            <div>
              <strong>Tests completed</strong>
              <span>Verification ran as part of the orchestrated task. Check the Jobs view for detailed results.</span>
            </div>
            <span className="status-label green">passed</span>
          </div>
          <button className="secondary-button" disabled>
            <Play size={15} /> Run tests unavailable
          </button>
        </div>
      ) : (
        <div className="data-section">
          <div className="empty-run">
            <div className="empty-run-icon"><TestTube2 size={21} /></div>
            <div>
              <strong>No test results yet</strong>
              <span>Submit an orchestrated task with allow_tests enabled to see results here.</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsView() {
  const [compactMode, setCompactMode] = useState(true);
  const [confirmRisk, setConfirmRisk] = useState(true);
  const [saveHistory, setSaveHistory] = useState(true);

  return (
    <div className="settings-layout">
      <section className="data-section">
        <SectionHeading eyebrow="Interface" title="Workspace preferences" />
        <ToggleRow
          title="Compact task context"
          detail="Favor concise machine and repository summaries."
          checked={compactMode}
          onChange={setCompactMode}
        />
        <ToggleRow
          title="Confirm risky plans"
          detail="Ask before authorizing downgraded or limited profiles."
          checked={confirmRisk}
          onChange={setConfirmRisk}
        />
        <ToggleRow
          title="Save local run history"
          detail="Keep task metadata and redacted traces in browser state."
          checked={saveHistory}
          onChange={setSaveHistory}
        />
      </section>
      <section className="data-section">
        <SectionHeading eyebrow="Connection" title="Backend status" />
        <div className="disconnected-state">
          <div>
            <CircleOff size={20} />
          </div>
          <strong>Patch apply and test execution disabled</strong>
          <span>
            Direct patch application, file writes, and test execution require
            explicit user authorization via the backend API.
          </span>
          <ConnectionBadge state="disabled" />
        </div>
      </section>
    </div>
  );
}

function PageIntro({
  eyebrow,
  title,
  detail,
  state,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  state: "connected" | "mock" | "disabled";
}) {
  return (
    <section className="page-intro">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{detail}</p>
      </div>
      <ConnectionBadge state={state} />
    </section>
  );
}

function SectionHeading({
  eyebrow,
  title,
}: {
  eyebrow: string;
  title: string;
}) {
  return (
    <div className="section-heading compact">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
    </div>
  );
}

function StatBlock({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="stat-block">
      <div className="stat-icon">
        <Icon size={19} />
      </div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub}</small>
    </div>
  );
}

function ProfileItem({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function PolicyRow({
  title,
  value,
  tone,
}: {
  title: string;
  value: string;
  tone: string;
}) {
  return (
    <div className="policy-row">
      <span>{title}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function EvidenceRow({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children: string;
}) {
  return (
    <div className="evidence-row">
      <span>
        <strong>{label}</strong>
        <small>{children}</small>
      </span>
      <b>{value}</b>
    </div>
  );
}

function SignalRow({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="signal-row">
      <span>
        <strong>{label}</strong>
        <small>{detail}</small>
      </span>
      <b>{value}</b>
    </div>
  );
}

function DecisionBadge({
  decision,
}: {
  decision: "allow" | "downgrade" | "block";
}) {
  return (
    <span className={`decision-badge decision-badge-${decision}`}>
      {decision === "allow" ? (
        <Check size={13} />
      ) : decision === "downgrade" ? (
        <AlertTriangle size={13} />
      ) : (
        <CircleStop size={13} />
      )}
      {decision}
    </span>
  );
}

function summarizePlan(
  plan: Record<string, unknown> | undefined,
  fallback: string,
) {
  if (!plan || Object.keys(plan).length === 0) return fallback;
  const strategy = String(plan.strategy ?? "plan").replace(/_/g, " ");
  const size = plan.model_size_billion_params;
  if (typeof size === "number") return `${size}B ${strategy}`;
  if (plan.use_quantized_model) return `quantized ${strategy}`;
  return strategy;
}

function ToggleRow({
  title,
  detail,
  checked,
  onChange,
}: {
  title: string;
  detail: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="toggle-row">
      <span>
        <strong>{title}</strong>
        <small>{detail}</small>
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="toggle-track">
        <span />
      </span>
    </label>
  );
}

function isWorkflowRunning(workflowState: AstraWorkflowState) {
  return !["idle", "completed", "blocked", "failed"].includes(
    workflowState.stage,
  );
}

export default App;
