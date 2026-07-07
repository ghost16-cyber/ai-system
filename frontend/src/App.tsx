import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleOff,
  CircleStop,
  Code2,
  Command,
  Cpu,
  Database,
  FileDiff,
  File,
  FileCode2,
  FileJson,
  FileText,
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
  executionProfiles,
  featureConnections,
  orchestratorJobs,
  patchProposals,
  recentRuns,
  repositoryTree,
  runtimeContext,
  runtimeEvidence,
  runtimeResearchManifest,
  testRun,
  toolCalls,
  traceEvents,
  workflowScenarios,
} from "./data/mockData";
import { useAstraWorkflow } from "./hooks/useAstraWorkflow";
import type {
  AstraWorkflowState,
  ExecutionProfile,
  NavigationId,
  RepositoryNode,
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
  dashboard: ["Astra overview", "Product shell / centralized mock data"],
  workspace: ["Task workspace", "Standalone workflow / no backend requests"],
  runtime: ["Runtime intelligence", "Mock hardware and deterministic policy"],
  specialists: ["Specialists", "Backend specialist lifecycle and traces"],
  profiles: ["Execution profiles", "Validated plans compiled into settings"],
  traces: ["Trace audit", "Visible planning, policy, and tool decisions"],
  repository: ["Repository explorer", "Static fixture / no local file access"],
  patches: ["Patch review", "Review only / apply actions disabled"],
  tests: ["Test results", "Simulated verification output"],
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
  const [selectedProfile, setSelectedProfile] = useState(0);
  const [selectedPatch, setSelectedPatch] = useState(0);
  const [selectedPath, setSelectedPath] = useState(
    "backend/app/local_runtime/execution_profiles.py",
  );
  const workflow = useAstraWorkflow();

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
              <span>Mock machine ready</span>
            </div>
            <strong>{runtimeContext.machine.gpu}</strong>
            <span>
              {runtimeContext.machine.vramGb} GB VRAM /{" "}
              {runtimeContext.machine.ramGb} GB RAM
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
            <ConnectionBadge state="connected" />
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
            />
          )}
          {activeNav === "workspace" && (
            <WorkspaceView
              prompt={prompt}
              setPrompt={setPrompt}
              taskKind={taskKind}
              setTaskKind={setTaskKind}
              workflowState={workflow.state}
              startRun={() => workflow.submit(prompt, taskKind)}
              resetRun={workflow.reset}
              selectedRun={selectedRun}
              setSelectedRun={setSelectedRun}
              onNavigate={navigate}
              onOpenProfile={(profileId) => {
                const index = executionProfiles.findIndex(
                  (profile) => profile.id === profileId,
                );
                if (index >= 0) setSelectedProfile(index);
                navigate("profiles");
              }}
            />
          )}
          {activeNav === "runtime" && <RuntimeView />}
          {activeNav === "specialists" && <SpecialistsView />}
          {activeNav === "profiles" && (
            <ProfilesView
              selected={selectedProfile}
              setSelected={setSelectedProfile}
            />
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
}: {
  onNavigate: (id: NavigationId) => void;
  workflowState: AstraWorkflowState;
}) {
  return (
    <div className="page-stack">
      <section className="dashboard-strip">
        <div>
          <span className="eyebrow">Phase 16</span>
          <h2>Astra control center</h2>
          <p>
            The product shell now simulates research-backed runtime decisions.
            Backend authority remains intentionally limited.
          </p>
        </div>
        <button className="primary-button" onClick={() => onNavigate("workspace")}>
          <Plus size={16} /> New task
        </button>
      </section>

      <div className="stats-grid">
        <StatBlock
          icon={Activity}
          label="Recent runs"
          value="3"
          sub="2 passed / 1 downgraded"
        />
        <StatBlock
          icon={ShieldCheck}
          label="Runtime policy"
          value="Low-VRAM"
          sub="Deterministic mock gate"
        />
        <StatBlock
          icon={FileDiff}
          label="Patch queue"
          value="2"
          sub="Apply remains disabled"
        />
        <StatBlock
          icon={TestTube2}
          label="Verification"
          value="156 passed"
          sub="Simulated latest run"
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
  startRun: () => void;
  resetRun: () => void;
  selectedRun: number;
  setSelectedRun: (value: number) => void;
  onNavigate: (id: NavigationId) => void;
  onOpenProfile: (profileId: string | null) => void;
};

function WorkspaceView({
  prompt,
  setPrompt,
  taskKind,
  setTaskKind,
  workflowState,
  startRun,
  resetRun,
  selectedRun,
  setSelectedRun,
  onNavigate,
  onOpenProfile,
}: WorkspaceProps) {
  return (
    <div className="workspace-grid">
      <section className="workspace-main">
        <div className="section-heading">
          <div>
            <span className="eyebrow">New task</span>
            <h2>What should Astra work on?</h2>
          </div>
          <ConnectionBadge state="mock" />
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
                onClick={
                  isWorkflowRunning(workflowState) ? undefined : startRun
                }
                disabled={isWorkflowRunning(workflowState) || !prompt.trim()}
              >
                {isWorkflowRunning(workflowState) ? (
                  <>
                    <Activity size={17} className="spin-soft" /> Planning
                  </>
                ) : (
                  <>
                    <Play size={16} fill="currentColor" /> Run mock task
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
          onOpenProfile={() => onOpenProfile(workflowState.activeProfileId)}
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
        <RuntimeSummary />
        <ProfileSummary
          profile={
            executionProfiles.find(
              (profile) => profile.id === workflowState.activeProfileId,
            ) ??
            (workflowState.stage === "idle" ? executionProfiles[1] : null)
          }
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
            This mock workflow previews runtime validation, profile compilation,
            tool calls, patch review, tests, and final response.
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
              ? "Mock orchestration"
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
                  : "Mock workflow ready"}
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

function RuntimeSummary() {
  return (
    <section className="rail-section">
      <div className="rail-heading">
        <div>
          <span className="eyebrow">Runtime</span>
          <h3>This machine</h3>
        </div>
        <ConnectionBadge state="mock" compact />
      </div>
      <div className="hardware-name">
        <div className="hardware-icon">
          <Cpu size={20} />
        </div>
        <div>
          <strong>{runtimeContext.machine.gpu}</strong>
          <span>CUDA available</span>
        </div>
      </div>
      <MetricRow
        icon={MemoryStick}
        label="VRAM"
        value={`${runtimeContext.machine.vramGb} GB`}
        detail="Low-VRAM mode"
        percent={76}
        tone="amber"
      />
      <MetricRow
        icon={Cpu}
        label="System RAM"
        value={`${runtimeContext.machine.ramGb} GB`}
        detail="20.4 GB available"
        percent={37}
        tone="green"
      />
      <MetricRow
        icon={HardDrive}
        label="Storage"
        value="512 GB"
        detail={`${runtimeContext.machine.storageFreeGb} GB available`}
        percent={64}
        tone="blue"
      />
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

function RuntimeView() {
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Machine intelligence"
        title="Hardware, research, policy, and toolchain"
        detail="Representative runtime context plus research evidence from the system information reports."
        state="mock"
      />
      <div className="stats-grid">
        <StatBlock
          icon={Cpu}
          label="CPU"
          value={runtimeContext.machine.cpu}
          sub={`${runtimeContext.machine.logicalCores} logical cores`}
        />
        <StatBlock
          icon={Zap}
          label="GPU"
          value="RTX 3050"
          sub="CUDA / 4 GB VRAM"
        />
        <StatBlock
          icon={MemoryStick}
          label="Memory"
          value="32 GB"
          sub="20.4 GB available"
        />
        <StatBlock
          icon={HardDrive}
          label="Workspace"
          value="186 GB free"
          sub="NVMe storage"
        />
      </div>
      <div className="runtime-layout">
        <section className="data-section">
          <SectionHeading eyebrow="Policy" title="Runtime decisions" />
          <div className="policy-list">
            <PolicyRow title="Low-VRAM mode" value="Enabled" tone="amber" />
            <PolicyRow title="Quantized models" value="Preferred" tone="green" />
            <PolicyRow title="Large local models" value="Restricted" tone="red" />
            <PolicyRow title="CPU fallback" value="Allowed" tone="green" />
            <PolicyRow
              title="RAG before fine-tuning"
              value="Preferred"
              tone="blue"
            />
          </div>
        </section>
        <section className="data-section">
          <SectionHeading eyebrow="Toolchain" title="Detected software" />
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
        </section>
      </div>
      <div className="runtime-audit-grid">
        <section className="data-section">
          <SectionHeading eyebrow="Research baseline" title="Evidence applied" />
          <div className="manifest-card">
            <strong>{runtimeResearchManifest.hardwareBaseline.gpu}</strong>
            <span>
              {runtimeResearchManifest.hardwareBaseline.vramGb} GB VRAM / CUDA{" "}
              {runtimeResearchManifest.hardwareBaseline.cudaAvailable
                ? "available"
                : "unavailable"}{" "}
              / {runtimeResearchManifest.sourceFolder}
            </span>
            <small>{runtimeResearchManifest.usageNote}</small>
          </div>
          <div className="evidence-list">
            {runtimeEvidence.map((item) => (
              <EvidenceRow key={item.id} label={item.label} value={item.value}>
                {item.detail}
              </EvidenceRow>
            ))}
          </div>
        </section>
        <section className="data-section">
          <SectionHeading eyebrow="Plan validation" title="Scenario gates" />
          <div className="scenario-list">
            {workflowScenarios.map((scenario) => (
              <div className="scenario-row" key={scenario.id}>
                <div>
                  <strong>{scenario.title}</strong>
                  <span>{scenario.validation.reason}</span>
                </div>
                <DecisionBadge decision={scenario.validation.decision} />
              </div>
            ))}
          </div>
        </section>
      </div>
      <section className="data-section">
        <SectionHeading eyebrow="AI architecture" title="Coordinator, specialists, policy" />
        <div className="ai-stack-panel ai-stack-panel-flat">
          <SignalRow
            label="SLM coordinator"
            value="understand / plan / explain"
            detail="The SLM proposes actions and explanations but does not execute tools."
          />
          <SignalRow
            label="Specialist models"
            value="predict / classify / rank"
            detail="Intent and error classifiers are advisory v1 signals shaped for future local training."
          />
          <SignalRow
            label="Deterministic backend"
            value="approve / block / verify"
            detail="Runtime gates, patch validation, and tool policy remain the final authority."
          />
          <SignalRow
            label="Frontend"
            value="review / approve"
            detail="The control center displays decisions while dangerous actions stay disabled."
          />
        </div>
      </section>
    </div>
  );
}

function ProfilesView({
  selected,
  setSelected,
}: {
  selected: number;
  setSelected: (index: number) => void;
}) {
  const profile = executionProfiles[selected];
  return (
    <div className="split-view">
      <section className="data-section browser-pane">
        <SectionHeading eyebrow="Mock profiles" title="Available profiles" />
        <div className="profile-browser">
          {executionProfiles.map((item, index) => (
            <button
              key={item.id}
              className={`profile-browser-row ${
                selected === index ? "selected" : ""
              }`}
              onClick={() => setSelected(index)}
            >
              <span className={`run-icon profile-${item.status}`}>
                <Layers3 size={17} />
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>
                  {item.runtime} / {item.device}
                </small>
              </span>
              <span className={`status-label profile-${item.status}`}>
                {item.status}
              </span>
            </button>
          ))}
        </div>
      </section>
      <section className="data-section detail-pane">
        <div className="detail-title-row">
          <div>
            <span className="eyebrow">{profile.taskType}</span>
            <h2>{profile.name}</h2>
            <p>{profile.strategy}</p>
          </div>
          <ConnectionBadge state="mock" />
        </div>
        <div className="profile-overview-grid">
          <ProfileItem term="Runtime" value={profile.runtime} />
          <ProfileItem term="Device" value={profile.device.toUpperCase()} />
          <ProfileItem term="Status" value={profile.status} />
          <ProfileItem term="Source" value="Validated active plan" />
        </div>
        <h3 className="subsection-title">Execution settings</h3>
        <div className="settings-table">
          {profile.settings.map((setting) => (
            <div key={setting.label}>
              <span>{setting.label}</span>
              <strong>{setting.value}</strong>
            </div>
          ))}
        </div>
        <h3 className="subsection-title">Safeguards</h3>
        <ul className="safeguard-list">
          {profile.safeguards.map((item) => (
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
  const events =
    workflowState.traceEvents.length > 0
      ? workflowState.traceEvents
      : traceEvents;
  const decision = workflowState.decision ?? "downgrade";
  const requestedPlan = summarizePlan(
    workflowState.validation?.requestedPlan,
    "8B local inference",
  );
  const activePlan =
    decision === "block"
      ? "blocked before execution"
      : summarizePlan(
          workflowState.validation?.recommendedPlan,
          "3B quantized inference",
        );
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Auditability"
        title="Runtime and orchestration trace"
        detail="Every mock decision is visible before future backend integration."
        state="mock"
      />
      <section className="trace-layout">
        <div className="trace-summary">
          <DecisionBadge decision={decision} />
          <div>
            <strong>{workflowState.task || "Prepare local code model"}</strong>
            <span>
              {workflowState.runId ?? "run-model"} /{" "}
              {workflowState.stage === "idle"
                ? "completed in 2.0 sec"
                : workflowState.stage.replace(/_/g, " ")}
            </span>
          </div>
          <span className="trace-summary-spacer" />
          <ProfileItem term="Requested" value={requestedPlan} />
          <ProfileItem term="Active plan" value={activePlan} />
        </div>
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
  return (
    <div className="repository-layout">
      <section className="data-section repository-tree-pane">
        <div className="section-heading compact">
          <div>
            <span className="eyebrow">Static fixture</span>
            <h2>ai-system-1</h2>
          </div>
          <ConnectionBadge state="mock" compact />
        </div>
        <div className="repo-filter">
          <Search size={15} />
          <input placeholder="Filter files" aria-label="Filter files" />
        </div>
        <div className="tree">
          {repositoryTree.map((node) => (
            <TreeNode
              key={node.path}
              node={node}
              depth={0}
              selectedPath={selectedPath}
              onSelect={setSelectedPath}
            />
          ))}
        </div>
      </section>
      <section className="data-section file-preview-pane">
        <div className="file-preview-header">
          <div>
            <span className="eyebrow">Selected file</span>
            <h2>
              {selectedPath.split("/")[selectedPath.split("/").length - 1]}
            </h2>
            <p>{selectedPath}</p>
          </div>
          <ConnectionBadge state="mock" />
        </div>
        <pre className="code-preview">
          <code>{mockFileContent(selectedPath)}</code>
        </pre>
        <div className="file-action-bar">
          <span>
            Repository reading is disabled until a backend workspace contract is
            connected.
          </span>
          <button className="secondary-button" disabled>
            Open file
          </button>
        </div>
      </section>
    </div>
  );
}

function TreeNode({
  node,
  depth,
  selectedPath,
  onSelect,
}: {
  node: RepositoryNode;
  depth: number;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  const Icon =
    node.kind === "folder"
      ? Folder
      : node.kind === "python"
        ? FileCode2
        : node.kind === "markdown"
          ? FileText
          : node.kind === "json"
            ? FileJson
            : File;
  return (
    <>
      <button
        className={`tree-row ${selectedPath === node.path ? "selected" : ""}`}
        style={{ paddingLeft: `${8 + depth * 17}px` }}
        onClick={() => onSelect(node.path)}
      >
        <Icon size={15} />
        <span>{node.name}</span>
        {node.state && <small className={`file-state ${node.state}`} />}
      </button>
      {node.children?.map((child) => (
        <TreeNode
          key={child.path}
          node={child}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
    </>
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
  const patch = patchProposals[selected];
  return (
    <div className="split-view">
      <section className="data-section browser-pane">
        <SectionHeading eyebrow="Review queue" title="Patch proposals" />
        <div className="patch-browser">
          {patchProposals.map((item, index) => (
            <button
              key={item.id}
              className={`patch-browser-row ${
                index === selected ? "selected" : ""
              }`}
              onClick={() => setSelected(index)}
            >
              <span
                className={`decision-icon ${
                  item.status === "blocked"
                    ? "decision-block"
                    : "decision-downgrade"
                }`}
              >
                <FileDiff size={15} />
              </span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.file}</small>
              </span>
              <span className={`risk-label risk-${item.risk}`}>{item.risk}</span>
            </button>
          ))}
        </div>
      </section>
      <section className="data-section detail-pane">
        <div className="detail-title-row">
          <div>
            <span className="eyebrow">{patch.id}</span>
            <h2>{patch.title}</h2>
            <p>
              {patch.file} / {patch.changedLines} changed lines
            </p>
          </div>
          <ConnectionBadge state="disabled" />
        </div>
        <div className="diff-view">
          <pre className="diff-old">
            <code>{patch.oldCode}</code>
          </pre>
          <pre className="diff-new">
            <code>{patch.newCode}</code>
          </pre>
        </div>
        <h3 className="subsection-title">Safety checks</h3>
        <ul className="safeguard-list">
          {patch.checks.map((check) => (
            <li key={check}>
              {patch.status === "blocked" ? (
                <AlertTriangle size={15} />
              ) : (
                <CheckCircle2 size={15} />
              )}
              <span>{check}</span>
            </li>
          ))}
        </ul>
        <div className="disabled-action-row">
          <span>
            {workflowState.patchVisible
              ? "The workflow produced this mock proposal. Apply remains disabled until backend authorization is connected."
              : "No current workflow patch exists. This fixture demonstrates the future review surface."}
          </span>
          <button className="primary-button" disabled>
            Apply patch
          </button>
        </div>
      </section>
    </div>
  );
}

function TestsView({
  workflowState,
}: {
  workflowState: AstraWorkflowState;
}) {
  const displayStatus = workflowState.testsRunning
    ? "running"
    : workflowState.testsVisible
      ? "passed"
      : testRun.status;
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Verification"
        title="Latest test result"
        detail="Representative test output shaped for the future job contract."
        state="mock"
      />
      <div className="test-summary-grid">
        <StatBlock
          icon={CheckCircle2}
          label="Passed"
          value={
            workflowState.testsRunning
              ? "..."
              : String(workflowState.testsVisible ? testRun.passed : testRun.passed)
          }
          sub={
            workflowState.testsVisible
              ? "Produced by current mock workflow"
              : "Fixture from latest mock run"
          }
        />
        <StatBlock
          icon={CircleStop}
          label="Failed"
          value={String(testRun.failed)}
          sub="No active failures"
        />
        <StatBlock
          icon={Activity}
          label="Duration"
          value={testRun.duration}
          sub="Mock execution time"
        />
        <StatBlock
          icon={TerminalSquare}
          label="Command"
          value="pytest"
          sub="Execution disabled"
        />
      </div>
      <section className="data-section">
        <div className="test-command">
          <TerminalSquare size={16} />
          <code>{testRun.command}</code>
          <span className={`status-label ${displayStatus === "passed" ? "green" : "amber"}`}>
            {displayStatus}
          </span>
        </div>
        <div className="suite-list">
          {testRun.suites.map((suite) => (
            <div className="suite-row" key={suite.name}>
              <span className="decision-icon decision-allow">
                <Check size={14} />
              </span>
              <div>
                <strong>{suite.name}</strong>
                <span>{suite.detail}</span>
              </div>
              <span className="status-label green">{suite.status}</span>
            </div>
          ))}
        </div>
        <button className="secondary-button" disabled>
          <Play size={15} /> Run tests unavailable
        </button>
      </section>
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
          <strong>Backend intentionally disconnected</strong>
          <span>
            No network requests, patch actions, file reads, commands, or model
            workloads are available from this frontend.
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

function mockFileContent(path: string) {
  if (path.endsWith("execution_profiles.py")) {
    return `def build_execution_profile(task, runtime_context, active_plan):
    """Compile an approved plan into machine-specific settings."""
    if not active_plan:
        raise ValueError("active validated plan required")

    return ExecutionProfile(
        task_type=classify_task(task),
        source_plan=active_plan,
        safeguards=["dry-run before execution"],
    )`;
  }
  if (path.endsWith("planning_rules.py")) {
    return `def validate_task_plan(task, requested_plan, runtime_context):
    if runtime_context.policy.low_vram_mode:
        return downgrade_large_plan(requested_plan)
    return allow(requested_plan)`;
  }
  if (path.endsWith("App.tsx")) {
    return `export default function App() {
  return <AstraProductShell connection="connected" />;
}`;
  }
  return `# Static frontend preview
# Selected path: ${path}
# Real repository reads are not connected.`;
}

function isWorkflowRunning(workflowState: AstraWorkflowState) {
  return !["idle", "completed", "blocked", "failed"].includes(
    workflowState.stage,
  );
}

export default App;
