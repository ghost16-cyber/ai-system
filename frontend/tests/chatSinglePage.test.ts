import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("the application shell renders only the chat product", () => {
  assert.match(appSource, /aria-label="Conversation"/);
  assert.match(appSource, />New chat</);
  assert.match(appSource, /Backend connected/);
  assert.doesNotMatch(appSource, /type PageId/);
  assert.doesNotMatch(appSource, /Current guardrails/);
  assert.doesNotMatch(appSource, /activePage/);
  assert.doesNotMatch(appSource, />Assignments</);
  assert.doesNotMatch(appSource, />System</);
  assert.doesNotMatch(appSource, />History</);
  assert.doesNotMatch(appSource, />Settings</);
});

test("ordinary chat and command cards remain in the single conversation", () => {
  assert.match(appSource, /client\.streamChat/);
  assert.match(appSource, /Approve and run/);
  assert.match(appSource, /client\.cancelAssignmentCommand/);
  assert.match(appSource, /Technical details/);
  assert.match(appSource, /<details className="technical">/);
});

test("conversation leaves scroll clearance for the fixed composer", () => {
  assert.match(stylesSource, /--composer-clearance:\s*260px/);
  assert.match(stylesSource, /\.conversation\s*\{[^}]*padding-bottom:\s*var\(--composer-clearance\)/s);
  assert.match(stylesSource, /\.conversation\s*\{[^}]*scroll-padding-bottom:\s*var\(--composer-clearance\)/s);
  assert.match(stylesSource, /\.composer\s*\{[^}]*position:\s*fixed/s);
});

test("completed command output is bounded and scrollable", () => {
  assert.match(appSource, /Full redacted stdout/);
  assert.match(appSource, /Full redacted stderr/);
  assert.match(stylesSource, /\.action-card \.technical-body label pre\s*\{[^}]*max-height:\s*220px/s);
  assert.match(stylesSource, /\.action-card \.technical-body label pre\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(stylesSource, /\.technical pre, \.json-block\s*\{[^}]*white-space:\s*pre-wrap/s);
});

test("command card uses friendly workspace text and keeps raw path in details", () => {
  assert.match(appSource, /Working directory: <span className="friendly-location">Project workspace<\/span>/);
  assert.match(appSource, /Workspace details/);
  assert.match(appSource, /Resolved working directory: <code>\{plan\.workspace \|\| "\."\}<\/code>/);
  assert.doesNotMatch(appSource, /Working directory: <code>\{plan\.workspace/);
});

test("raw action data and technical details are not open by default", () => {
  assert.match(stylesSource, /\.action-card \.technical-body > \.json-block\s*\{[^}]*display:\s*none/s);
  assert.doesNotMatch(appSource, /<details[^>]*\sopen/);
  assert.doesNotMatch(appSource, /open=\{/);
});

test("removed product areas have chat-native request handlers", () => {
  for (const phrase of [
    "show system status",
    "show recent chats",
    "what model are you using",
    "show rag status",
    "show settings",
    "change the selected model",
    "read this assignment",
    "check assignment readiness",
  ]) {
    assert.ok(appSource.includes(phrase), `missing chat-native handler for: ${phrase}`);
  }
});
