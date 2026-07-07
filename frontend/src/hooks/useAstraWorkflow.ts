import { useEffect, useReducer } from "react";
import { scenarioForTask } from "../data/mockData";
import {
  astraWorkflowReducer,
  initialAstraWorkflowState,
  isWorkflowActive,
} from "../state/astraWorkflowState";
import type { TaskKind } from "../types/contracts";

export function useAstraWorkflow() {
  const [state, dispatch] = useReducer(
    astraWorkflowReducer,
    initialAstraWorkflowState,
  );

  useEffect(() => {
    if (!isWorkflowActive(state.stage)) return;
    const timer = window.setTimeout(() => dispatch({ type: "advance" }), 620);
    return () => window.clearTimeout(timer);
  }, [state.stage]);

  function submit(task: string, taskKind: TaskKind) {
    const scenario = scenarioForTask(taskKind, task);
    dispatch({
      type: "submit",
      task,
      taskKind,
      runId: `mock-${Date.now()}`,
      scenario,
    });
  }

  return {
    state,
    submit,
    reset: () => dispatch({ type: "reset" }),
    fail: (error: string) => dispatch({ type: "fail", error }),
  };
}
