import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  chatProjectRequestField,
  deriveProjectOptions,
  resolveActiveProjectSelection,
} from "../src/state/chatProjectSelectionState.ts";
import type { CanonicalProjectResponse } from "../src/types/contracts.ts";

function project(overrides: Record<string, unknown> = {}): CanonicalProjectResponse {
  return {
    schema_version: "astra.project-api.project.v1",
    project: {
      schema_version: "astra.project-control.read-model.v1",
      project_run_id: "project-1",
      conversation_id: "conversation-1",
      workspace_id: "workspace-1",
      repository_root_fingerprint: "root-fingerprint-1",
      ...overrides,
    },
    artifacts: [],
    coordinator: null,
    next_permitted_actions: [],
  } as unknown as CanonicalProjectResponse;
}

test("deriveProjectOptions returns an empty list for a missing or malformed projects value", () => {
  assert.deepEqual(deriveProjectOptions(undefined), []);
  assert.deepEqual(deriveProjectOptions(null), []);
  assert.deepEqual(deriveProjectOptions([{} as CanonicalProjectResponse]), []);
});

test("deriveProjectOptions drops entries with no usable project_run_id rather than crashing", () => {
  const options = deriveProjectOptions([
    project({ project_run_id: "project-1" }),
    {} as CanonicalProjectResponse,
    project({ project_run_id: "" }),
  ]);
  assert.deepEqual(options.map((option) => option.projectRunId), ["project-1"]);
});

test("deriveProjectOptions labels a project with its repository fingerprint first", () => {
  const [option] = deriveProjectOptions([
    project({ project_run_id: "project-1", repository_root_fingerprint: "my-repo", workspace_id: "workspace-1" }),
  ]);
  assert.equal(option.label, "my-repo");
});

test("deriveProjectOptions falls back to workspace_id when there is no repository fingerprint", () => {
  const [option] = deriveProjectOptions([
    project({ project_run_id: "project-1", repository_root_fingerprint: "", workspace_id: "workspace-1" }),
  ]);
  assert.equal(option.label, "workspace-1");
});

test("deriveProjectOptions falls back to a shortened project_run_id when nothing else is available", () => {
  const [option] = deriveProjectOptions([
    project({ project_run_id: "project-run-abcdefgh", repository_root_fingerprint: "", workspace_id: "" }),
  ]);
  assert.equal(option.label, "project-…");
});

test("deriveProjectOptions sorts options by label", () => {
  const options = deriveProjectOptions([
    project({ project_run_id: "project-b", repository_root_fingerprint: "zeta" }),
    project({ project_run_id: "project-a", repository_root_fingerprint: "alpha" }),
  ]);
  assert.deepEqual(options.map((option) => option.projectRunId), ["project-a", "project-b"]);
});

test("resolveActiveProjectSelection reports no selection when nothing is stored", () => {
  assert.deepEqual(resolveActiveProjectSelection(null, []), { projectRunId: null, stale: false });
  assert.deepEqual(resolveActiveProjectSelection(undefined, []), { projectRunId: null, stale: false });
});

test("resolveActiveProjectSelection accepts a stored selection that is among the live projects", () => {
  const projects = [project({ project_run_id: "project-1" })];
  assert.deepEqual(resolveActiveProjectSelection("project-1", projects), {
    projectRunId: "project-1", stale: false,
  });
});

test("resolveActiveProjectSelection treats a stored selection absent from live projects as stale", () => {
  const projects = [project({ project_run_id: "project-1" })];
  assert.deepEqual(resolveActiveProjectSelection("project-deleted", projects), {
    projectRunId: null, stale: true,
  });
});

test("chatProjectRequestField never attaches a stale selection to a new request", () => {
  assert.deepEqual(chatProjectRequestField({ projectRunId: "project-1", stale: true }), { project_run_id: null });
});

test("chatProjectRequestField attaches a resolved, non-stale selection", () => {
  assert.deepEqual(chatProjectRequestField({ projectRunId: "project-1", stale: false }), {
    project_run_id: "project-1",
  });
  assert.deepEqual(chatProjectRequestField({ projectRunId: null, stale: false }), { project_run_id: null });
});

test("App.tsx builds one shared project_run_id field and reuses it for both /chat/run and /chat/stream bodies", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /chatProjectRequestField/);
  assert.match(app, /projectRunIdField/);
  const occurrences = app.match(/project_run_id: projectRunIdField/g) ?? [];
  assert.equal(occurrences.length, 2);
});

test("App.tsx hydrates the active project selection defensively via the typed state module", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /resolveActiveProjectSelection/);
  assert.match(app, /deriveProjectOptions/);
});

test("the chat client exposes setActiveProject against the active-project endpoint", () => {
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.match(client, /setActiveProject/);
  assert.match(client, /active-project/);
});
