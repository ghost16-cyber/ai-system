import type {
  AstraWorkflowAction,
  AstraWorkflowStage,
  AstraWorkflowState,
  TraceEvent,
  WorkflowScenario,
} from "../types/contracts";

export const initialAstraWorkflowState: AstraWorkflowState = {
  runId: null,
  task: "",
  taskKind: "Local SLM",
  scenarioId: null,
  stage: "idle",
  decision: null,
  validation: null,
  slmSignal: null,
  specialistSignals: [],
  activeProfile: null,
  activeProfileId: null,
  runtimeEvidence: [],
  policyExplanations: [],
  traceEvents: [],
  patchVisible: false,
  testsVisible: false,
  testsRunning: false,
  finalMessage: null,
  error: null,
};

export const workflowStageOrder: AstraWorkflowStage[] = [
  "idle",
  "planning",
  "runtime_checked",
  "plan_validated",
  "profile_built",
  "authorized",
  "running_mock",
  "completed",
];

export function astraWorkflowReducer(
  state: AstraWorkflowState,
  action: AstraWorkflowAction,
): AstraWorkflowState {
  if (action.type === "reset") return initialAstraWorkflowState;

  if (action.type === "fail") {
    return {
      ...state,
      stage: "failed",
      testsRunning: false,
      error: action.error,
      finalMessage: "The workflow stopped due to an error.",
      traceEvents: [
        ...state.traceEvents,
        trace(
          "failure",
          "Mock workflow failed",
          action.error,
          "blocked",
          elapsed(state.traceEvents.length),
        ),
      ],
    };
  }

  if (action.type === "submit") {
    return {
      ...initialAstraWorkflowState,
      runId: action.runId,
      task: action.task,
      taskKind: action.taskKind,
      scenarioId: action.scenario.id,
      stage: "planning",
      validation: action.scenario.validation,
      slmSignal: action.scenario.slmSignal,
      specialistSignals: action.scenario.specialistSignals,
      activeProfile: action.scenario.activeProfile,
      runtimeEvidence: action.scenario.runtimeEvidence,
      policyExplanations: action.scenario.policyExplanations,
      traceEvents: [
        trace(
          "planning",
          "Task accepted",
          `Scenario selected: ${action.scenario.title}.`,
          "active",
          "0.1s",
        ),
      ],
    };
  }

  if (action.type !== "advance") return state;

  const scenario = scenarioFromState(state);

  switch (state.stage) {
    case "planning":
      return {
        ...state,
        stage: "runtime_checked",
        traceEvents: [
          ...completeLast(state.traceEvents),
          trace(
            "runtime",
            "Runtime context checked",
            scenario.traceDetails.runtime,
            "active",
            elapsed(state.traceEvents.length),
          ),
        ],
      };

    case "runtime_checked": {
      const decision = scenario.validation.decision;
      const blocked = decision === "block";
      return {
        ...state,
        stage: blocked ? "blocked" : "plan_validated",
        decision,
        validation: scenario.validation,
        finalMessage: blocked ? scenario.finalMessage : null,
        traceEvents: [
          ...completeLast(state.traceEvents),
          trace(
            "research",
            "Research evidence applied",
            scenario.traceDetails.research,
            decision === "allow" ? "passed" : "warning",
            elapsed(state.traceEvents.length),
          ),
          trace(
            "gate",
            blocked
              ? "Plan blocked"
              : decision === "downgrade"
                ? "Plan downgraded"
                : "Plan allowed",
            scenario.traceDetails.gate,
            blocked ? "blocked" : decision === "downgrade" ? "warning" : "active",
            elapsed(state.traceEvents.length + 1),
          ),
        ],
      };
    }

    case "plan_validated":
      return {
        ...state,
        stage: "profile_built",
        activeProfile: scenario.activeProfile,
        activeProfileId: scenario.activeProfileId,
        traceEvents: [
          ...completeLast(state.traceEvents),
          trace(
            "profile",
            "Execution profile built",
            scenario.traceDetails.profile,
            "active",
            elapsed(state.traceEvents.length),
          ),
        ],
      };

    case "profile_built":
      return {
        ...state,
        stage: "authorized",
        traceEvents: [
          ...completeLast(state.traceEvents),
          trace(
            "authorization",
            "Profile authorized",
            scenario.traceDetails.authorization,
            "active",
            elapsed(state.traceEvents.length),
          ),
        ],
      };

    case "authorized":
      return {
        ...state,
        stage: "running_mock",
        patchVisible: scenario.patchVisible,
        testsVisible: scenario.testsVisible,
        testsRunning: scenario.testsVisible,
        traceEvents: [
          ...completeLast(state.traceEvents),
          trace(
            "tools",
            toolTitle(scenario),
            scenario.traceDetails.tools,
            "active",
            elapsed(state.traceEvents.length),
          ),
        ],
      };

    case "running_mock":
      return {
        ...state,
        stage: "completed",
        testsRunning: false,
        finalMessage: scenario.finalMessage,
        traceEvents: [
          ...completeLast(state.traceEvents),
          trace(
            "response",
            "Workflow completed",
            scenario.traceDetails.response,
            "passed",
            elapsed(state.traceEvents.length),
          ),
        ],
      };

    default:
      return state;
  }
}

export function isWorkflowActive(stage: AstraWorkflowStage) {
  return !["idle", "completed", "blocked", "failed"].includes(stage);
}

function scenarioFromState(state: AstraWorkflowState): WorkflowScenario {
  return {
    id: state.scenarioId ?? "fallback",
    taskKind: state.taskKind,
    title: state.taskKind,
    recommendedPrompt: state.task,
    requestedPlan: state.validation?.requestedPlan ?? {},
    validation:
      state.validation ?? {
        decision: "allow",
        allowed: true,
        reason: "Fallback mock validation.",
        blockedSignals: [],
        requestedPlan: {},
        recommendedPlan: {},
      },
    slmSignal:
      state.slmSignal ?? {
        model: "qwen2.5-coder:1.5b",
        role: "coordinator",
        proposedAction: "final_response",
        reason: "Fallback mock SLM signal.",
        advisoryOnly: true,
      },
    specialistSignals: state.specialistSignals,
    activeProfile: state.activeProfile,
    activeProfileId: state.activeProfileId,
    runtimeEvidence: state.runtimeEvidence,
    policyExplanations: state.policyExplanations,
    traceDetails: {
      runtime: "Runtime context checked.",
      research: "Research evidence applied.",
      gate: state.validation?.reason ?? "Plan checked.",
      profile: "Execution profile built.",
      authorization: "Authorization checked.",
      tools: "Tools checked.",
      response: state.finalMessage ?? "Workflow completed.",
    },
    patchVisible: state.patchVisible,
    testsVisible: state.testsVisible,
    finalMessage: state.finalMessage ?? "Workflow completed.",
  };
}

function toolTitle(scenario: WorkflowScenario) {
  if (scenario.taskKind === "Code repair") return "Running analysis and tests";
  if (scenario.taskKind === "RAG workflow") return "Indexing repository";
  if (scenario.taskKind === "Model training") return "Checking training gate";
  if (scenario.taskKind === "Classical ML") return "Running sklearn pipeline";
  return "Loading model";
}

function trace(
  phase: string,
  title: string,
  detail: string,
  status: TraceEvent["status"],
  elapsedValue: string,
): TraceEvent {
  return {
    id: `${phase}-${title.toLowerCase().replace(/ /g, "-")}`,
    phase,
    title,
    detail,
    status,
    elapsed: elapsedValue,
  };
}

function completeLast(events: TraceEvent[]) {
  return events.map((event, index) =>
    index === events.length - 1 && event.status === "active"
      ? { ...event, status: "passed" as const }
      : event,
  );
}

function elapsed(index: number) {
  return `${(0.6 + index * 0.5).toFixed(1)}s`;
}
