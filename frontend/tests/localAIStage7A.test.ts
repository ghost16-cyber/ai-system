import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("system status uses backend local-AI capabilities and disabled future status", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /getLocalAICapabilities/);
  assert.match(app, /getLocalAIFutureStatus/);
  assert.match(app, /getLocalAIProviders/);
  assert.match(app, /getLocalAIModels/);
  assert.match(app, /getLocalAIScheduler/);
  assert.match(app, /future\.project_rag\.status/);
  assert.match(app, /future\.training\.status/);
  assert.match(app, /source_metadata\.install_command/);
  assert.doesNotMatch(app, /navigator\.gpu|WEBGL_debug_renderer_info/);
});

test("manual verification card renders exact backend criterion state", () => {
  const card = readFileSync(new URL("../src/components/ProjectControlCard.tsx", import.meta.url), "utf8");
  assert.match(card, /manual_evidence_required/);
  assert.match(card, /data-manual-criterion-id/);
  assert.match(card, /Handoff remains blocked/);
});

test("manual evidence and local AI mutations use typed backend clients", () => {
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.match(client, /submitManualEvidence/);
  assert.match(client, /verification\/manual-evidence/);
  assert.match(client, /astra\.project-api\.manual-evidence\.v1/);
});
