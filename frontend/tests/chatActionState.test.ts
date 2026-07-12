import assert from "node:assert/strict";
import test from "node:test";

import { actionFromPayload } from "../src/state/chatActionState.ts";

test("maps the persisted backend action payload to the shared chat action model", () => {
  const action = actionFromPayload({
    action_type: "command",
    title: "Run the project test suite",
    summary: "Runs tests.",
    steps: ["Approve", "Run"],
    safety_information: { approval_required: true },
    status: "awaiting_approval",
    approval_required: true,
    technical_details: {
      command_plan: { plan_id: "safe", command: "python -m pytest -q" },
    },
  });
  assert.equal(action?.status, "awaiting_approval");
  assert.equal(action?.commandPlan?.command, "python -m pytest -q");
  assert.equal(action?.approvalRequired, true);
});
