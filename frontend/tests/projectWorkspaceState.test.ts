import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { actionFromPayload } from "../src/state/chatActionState.ts";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const clientSource = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");

test("maps project patch and rollback action identity", () => {
  const patch = actionFromPayload({
    action_id: "patch-1",
    action_type: "project_patch",
    title: "Review patch",
    status: "awaiting_approval",
    approval_required: true,
    technical_details: { project_patch: { changes: [] } },
  });
  assert.equal(patch?.actionId, "patch-1");
  assert.equal(patch?.actionType, "project_patch");
  assert.equal(patch?.approvalRequired, true);
});

test("project evidence and expandable bounded diffs stay in chat", () => {
  assert.match(appSource, /function ProjectSources/);
  assert.match(appSource, /Project evidence/);
  assert.match(appSource, /<details key=\{change\.relative_path\} className="patch-file">/);
  assert.match(appSource, /className="patch-diff"/);
  assert.match(stylesSource, /\.patch-diff \{[^}]*max-height:[^}]*overflow: auto/s);
  assert.doesNotMatch(appSource, /project-management|project editor|approval page/i);
});

test("patch and rollback approvals use synchronous locks and exact confirmations", () => {
  assert.match(appSource, /tryLockCommandAction\(locks\.current, `project-patch:/);
  assert.match(appSource, /APPROVE PATCH \$\{patchId\}/);
  assert.match(appSource, /APPROVE ROLLBACK \$\{patchId\}/);
  assert.match(clientSource, /approveProjectPatch/);
  assert.match(clientSource, /applyProjectPatch/);
  assert.match(clientSource, /approveProjectRollback/);
});

test("connected project commands retain a separate approval lifecycle", () => {
  assert.match(appSource, /action\.actionType === "project_command"/);
  assert.match(appSource, /client\.approveProjectCommand/);
  assert.match(appSource, /client\.executeProjectCommand/);
  assert.match(appSource, /Approve and run/);
});

test("mobile project workflow remains within the accessible composer layout", () => {
  assert.match(stylesSource, /@media \(max-width: 640px\)[\s\S]*\.patch-diff/);
  assert.match(appSource, /<form className="composer"/);
  assert.match(appSource, /<section className="conversation" aria-label="Conversation">/);
});
