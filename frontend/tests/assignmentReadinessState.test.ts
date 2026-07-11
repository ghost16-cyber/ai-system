import assert from "node:assert/strict";
import test from "node:test";

import {
  requirementMatchesFilter,
  requirementStatusLabel,
} from "../src/state/assignmentReadinessState.ts";

test("maps API statuses to readable labels", () => {
  assert.equal(requirementStatusLabel("requires_manual_review"), "requires manual review");
  assert.equal(requirementStatusLabel("partially_verified"), "partially verified");
});

test("filters missing, failed, manual-review, partial, and verified states", () => {
  assert.equal(requirementMatchesFilter("missing", "missing"), true);
  assert.equal(requirementMatchesFilter("failed", "failed"), true);
  assert.equal(requirementMatchesFilter("requires_manual_review", "manual_review"), true);
  assert.equal(requirementMatchesFilter("partially_verified", "partially_verified"), true);
  assert.equal(requirementMatchesFilter("verified", "verified"), true);
  assert.equal(requirementMatchesFilter("detected", "verified"), false);
  assert.equal(requirementMatchesFilter("failed", "all"), true);
});
