import { useCallback, useEffect, useReducer, useRef } from "react";
import type { AstraClient, LocalAIModelConfiguration } from "../../../clients/astraClient";
import {
  classifyLocalAIError,
  initialLocalAIState,
  localAIReducer,
} from "../state/localAIState";

let requestSequence = 0;

function nextIdempotencyKey(prefix: string): string {
  requestSequence += 1;
  return `${prefix}-${Date.now()}-${requestSequence}`;
}

/** Thin orchestration glue only: every meaningful decision (status
 * classification, error classification, how the row is replaced after a
 * mutation) lives in the pure, independently tested `state/localAIState.ts`
 * reducer -- this hook just calls the existing Astra client and dispatches
 * what it returns. It never computes a configuration version, never
 * retries a failed request, and never guesses availability locally. */
export function useLocalAI(client: AstraClient, actorId: string) {
  const [state, dispatch] = useReducer(localAIReducer, initialLocalAIState);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    dispatch({ type: "load_start" });
    try {
      const [models, capabilities] = await Promise.all([
        client.getLocalAIModels(),
        client.getLocalAICapabilities(),
      ]);
      if (!mountedRef.current) return;
      dispatch({ type: "load_success", models: models.items, capabilities });
    } catch (error) {
      if (!mountedRef.current) return;
      dispatch({ type: "load_error", error: classifyLocalAIError(error) });
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    dispatch({ type: "refresh_start" });
    try {
      const capabilities = await client.refreshLocalAICapabilities({
        actor_id: actorId,
        expected_snapshot_id: null,
        idempotency_key: nextIdempotencyKey("local-ai-refresh"),
      });
      if (!mountedRef.current) return;
      dispatch({ type: "refresh_success", capabilities });
      await load();
    } catch (error) {
      if (!mountedRef.current) return;
      dispatch({ type: "refresh_error", error: classifyLocalAIError(error) });
    }
  }, [client, actorId, load]);

  const setModelEnabled = useCallback(
    async (model: LocalAIModelConfiguration, enabled: boolean) => {
      dispatch({ type: "model_action_start", modelProfileId: model.model_profile_id });
      try {
        const updated = await client.setLocalAIModelEnabled(model.model_profile_id, {
          actor_id: actorId,
          enabled,
          expected_configuration_version: model.configuration_version,
          idempotency_key: nextIdempotencyKey(
            `local-ai-${enabled ? "enable" : "disable"}-${model.model_profile_id}`,
          ),
        });
        if (!mountedRef.current) return;
        dispatch({ type: "model_action_success", model: updated });
      } catch (error) {
        if (!mountedRef.current) return;
        dispatch({
          type: "model_action_error", modelProfileId: model.model_profile_id,
          error: classifyLocalAIError(error),
        });
      }
    },
    [client, actorId],
  );

  const toggleDiagnostics = useCallback(() => dispatch({ type: "toggle_diagnostics" }), []);
  const dismissError = useCallback(() => dispatch({ type: "dismiss_error" }), []);

  return { state, reload: load, refresh, setModelEnabled, toggleDiagnostics, dismissError };
}
