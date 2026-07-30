import assert from "node:assert/strict";
import test from "node:test";

import { describeAstraError } from "../src/state/errorMessage.ts";

test("extracts detail.message from a retired-route error body", () => {
  const raw = JSON.stringify({
    detail: {
      schema_version: "astra.legacy-execution-retired.v1",
      code: "legacy_host_execution_retired",
      message: "Direct command execution has been retired.",
    },
  });
  assert.equal(describeAstraError(raw), "Direct command execution has been retired.");
});

test("extracts a plain string detail", () => {
  assert.equal(describeAstraError(JSON.stringify({ detail: "Not found" })), "Not found");
});

test("falls back to the raw text for non-JSON errors", () => {
  assert.equal(describeAstraError("network error"), "network error");
});

test("falls back to the raw text when JSON has no usable detail", () => {
  const raw = JSON.stringify({ status: 500 });
  assert.equal(describeAstraError(raw), raw);
});

test("falls back to the raw text for malformed JSON", () => {
  assert.equal(describeAstraError("{not-json"), "{not-json");
});
