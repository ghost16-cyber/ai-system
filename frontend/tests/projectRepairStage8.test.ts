import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const clientSource = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
const stateSource = readFileSync(new URL("../src/state/projectJobState.ts", import.meta.url), "utf8");

test("Stage 8 renders the bounded diagnosis and repair lifecycle in the existing chat card", () => {
  assert.match(appSource, /Diagnosis available/);
  assert.match(appSource, /bounded diagnosis are in progress/);
  assert.match(appSource, /Diagnosis completed\. Preparing an immutable repair preview/);
  assert.match(appSource, /failure output was redacted/);
  assert.match(appSource, /failure output was truncated/);
  assert.match(appSource, /Diagnosis needs clarification/);
  assert.match(appSource, /Diagnosis is plan-only/);
  assert.match(appSource, /Repair cycle limit reached/);
  assert.match(appSource, /The repair is ready for review/);
  assert.match(appSource, /Repair applied/);
  assert.match(appSource, /validation command has not been rerun/);
  assert.match(appSource, /awaiting separate approval/);
  assert.match(appSource, /separately approved validation/);
  assert.match(appSource, /latest repair was rolled back/);
  assert.doesNotMatch(appSource, /RepairDashboard|DiagnosisPage|ExecutionConsole/);
});

test("Stage 8 client state keeps identifiers, confidence, and safe relative paths", () => {
  assert.match(stateSource, /failureEvidenceId/);
  assert.match(stateSource, /commandExecutionId/);
  assert.match(stateSource, /confidenceReasons/);
  assert.match(stateSource, /failure_output_truncated/);
  assert.match(stateSource, /failure_redaction_count/);
  assert.match(stateSource, /safePaths\(repair\.affected_files\)/);
  assert.match(clientSource, /project_diagnosis_offered/);
  assert.match(clientSource, /project_repair_ready/);
  assert.match(clientSource, /project_repair_validated/);
});
