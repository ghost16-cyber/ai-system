import { useEffect, useReducer, useRef, useState } from "react";
import {
  apiClient,
  buildScenarioFromApiData,
  defaultPlanForTaskKind,
  fallbackValidation,
} from "../api/hooks";
import {
  astraWorkflowReducer,
  initialAstraWorkflowState,
  isWorkflowActive,
} from "../state/astraWorkflowState";
import type {
  ExecutionProfile,
  RuntimeContext,
  SpecialistRouteResult,
  TaskKind,
} from "../types/contracts";

export function useAstraWorkflow() {
  const [state, dispatch] = useReducer(
    astraWorkflowReducer,
    initialAstraWorkflowState,
  );
  const [submitting, setSubmitting] = useState(false);
  const abortRef = useRef(false);

  useEffect(() => {
    if (!isWorkflowActive(state.stage)) return;
    const timer = window.setTimeout(() => dispatch({ type: "advance" }), 620);
    return () => window.clearTimeout(timer);
  }, [state.stage]);

  async function submit(task: string, taskKind: TaskKind) {
    if (submitting) return;
    setSubmitting(true);
    abortRef.current = false;

    try {
      const runId = `run-${Date.now()}`;
      const defaultPlan = defaultPlanForTaskKind(taskKind);

      // Fetch runtime context, specialist routing, and plan validation in parallel
      const [context, routeResult, validation] = await Promise.all([
        apiClient
          .getRuntimeContext(task)
          .catch((): RuntimeContext | null => null),
        apiClient
          .routeSpecialistTask(task, false)
          .catch((): SpecialistRouteResult | null => null),
        apiClient
          .validateRuntimePlan({ task, taskKind, requestedPlan: defaultPlan })
          .catch(() => fallbackValidation(taskKind)),
      ]);

      if (abortRef.current) return;

      // Build execution profile if plan is not blocked
      let profile: ExecutionProfile | null = null;
      if (validation.decision !== "block") {
        profile = await apiClient
          .buildExecutionProfile({
            task,
            taskKind,
            requestedPlan: validation.recommendedPlan || defaultPlan,
          })
          .catch(() => null);
      }

      if (abortRef.current) return;

      const scenario = buildScenarioFromApiData({
        task,
        taskKind,
        context,
        validation,
        profile,
        routeResult,
      });

      dispatch({ type: "submit", task, taskKind, runId, scenario });
    } catch (e) {
      dispatch({ type: "fail", error: String(e) });
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    abortRef.current = true;
    dispatch({ type: "reset" });
  }

  return {
    state,
    submitting,
    submit,
    reset,
    fail: (error: string) => dispatch({ type: "fail", error }),
  };
}

