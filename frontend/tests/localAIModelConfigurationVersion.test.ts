import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("local AI model list contract exposes configuration_version and updated_at", () => {
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.match(client, /interface LocalAIModelConfiguration \{[\s\S]*?configuration_version: number;/);
  assert.match(client, /interface LocalAIModelConfiguration \{[\s\S]*?updated_at\?: string \| null;/);
});

test("local AI model enable/disable client method requires an explicit configuration version", () => {
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.match(client, /interface LocalAIModelEnableRequest \{[\s\S]*?expected_configuration_version: number;/);
  assert.match(client, /setLocalAIModelEnabled\(/);
  assert.match(client, /models\/\$\{encodeURIComponent\(modelProfileId\)\}\/enabled/);
});
