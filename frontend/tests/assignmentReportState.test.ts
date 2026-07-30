import assert from "node:assert/strict";
import test from "node:test";

import { exportReadinessIsBlocked, hasVisiblePlaceholders, reportSectionMatches, reportStateLabel, toggleSubmissionFile } from "../src/state/assignmentReportState.ts";

const section = (state: string, placeholders: string[] = [], warnings: string[] = []) => ({ verification_state: state, placeholders, warnings } as never);

test("maps report states and keeps stale/failed evidence visible", () => {
  assert.equal(reportStateLabel("manually_accepted"), "manually accepted");
  assert.equal(reportSectionMatches(section("stale"), "stale"), true);
  assert.equal(reportSectionMatches(section("unsupported", [], ["Validation failed"]), "failed"), true);
  assert.equal(reportSectionMatches(section("missing", ["[Required]"]), "placeholders"), true);
});

test("submission files begin empty and require explicit toggles", () => {
  const initial: string[] = [];
  const selected = toggleSubmissionFile(initial, "main.py");
  assert.deepEqual(initial, []);
  assert.deepEqual(selected, ["main.py"]);
  assert.deepEqual(toggleSubmissionFile(selected, "main.py"), []);
});

test("placeholder and export blockers remain explicit", () => {
  assert.equal(hasVisiblePlaceholders({ placeholders: ["[Required]"], grounded_content_blocks: [] } as never), true);
  assert.equal(exportReadinessIsBlocked({ status: "blocked", export_blockers: ["Missing section"] } as never), true);
  assert.equal(exportReadinessIsBlocked({ status: "eligible_for_final_human_submission_review", export_blockers: [] } as never), false);
});
