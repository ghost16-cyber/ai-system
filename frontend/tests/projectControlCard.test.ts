import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("canonical card renders only backend action descriptors", () => {
  const source = readFileSync(new URL("../src/components/ProjectControlCard.tsx", import.meta.url), "utf8");
  assert.match(source, /project\.nextPermittedActions\.map/);
  assert.match(source, /onAction\(action\)/);
  assert.doesNotMatch(source, /legacyLifecycle|pendingUserAction\s*===/);
});

test("canonical card exposes cancellation, coordinator, projection, artifact and repair state", () => {
  const source = readFileSync(new URL("../src/components/ProjectControlCard.tsx", import.meta.url), "utf8");
  for (const signal of ["cancellationStatus", "project.coordinator", "project.projection", "project.artifacts", "project.repairState"]) {
    assert.match(source, new RegExp(signal.replace(".", "\\.")));
  }
});
