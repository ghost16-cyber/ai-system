import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const clientSource = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
const workspaceStateSource = readFileSync(new URL("../src/state/assignmentWorkspaceState.ts", import.meta.url), "utf8");
const folderStateSource = readFileSync(new URL("../src/state/folderAccessState.ts", import.meta.url), "utf8");
const streamStateSource = readFileSync(new URL("../src/state/chatStreamState.ts", import.meta.url), "utf8");

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


test("command lifecycle uses the persistent backend chat run ID", () => {
  assert.match(appSource, /message\.run\?\.run_id/);
  assert.match(appSource, /chat_run_id:\s*chatRunId/);
});

test("assignment documents attach and upload through the chat composer", () => {
  assert.match(appSource, /type="file"/);
  assert.match(appSource, /accept="\.txt,\.md,\.docx"/);
  assert.match(appSource, /client\.uploadAssignment\(attachedFile\)/);
  assert.match(appSource, /Read assignment:/);
  assert.match(clientSource, /application\/octet-stream/);
  assert.match(clientSource, /\/assignments\/upload\?filename=/);
  assert.match(stylesSource, /\.attachment-chip\s*\{/);
});



test("assignment workspace creation is approval-gated and uses the real backend writer", () => {
  assert.match(appSource, /Create assignment workspace\?/);
  assert.match(workspaceStateSource, /create assignment workspace/);
  assert.match(appSource, />Create workspace</);
  assert.match(appSource, /client\.approveChatAssignmentWorkspace/);
  assert.match(appSource, /client\.cancelChatAssignmentWorkspace/);
  assert.match(appSource, /chat_run_id:\s*chatRunId/);
  assert.match(appSource, /No generated code will be executed/);
  assert.match(clientSource, /\/chat\/assignments\/workspace\/\$\{encodeURIComponent\(actionId\)\}\/approve/);
  assert.match(stylesSource, /\.workspace-plan-list\s*\{/);
});

test("workspace creation requests use the native assignment interceptor", () => {
  const nativeHandler = appSource.indexOf("isAssignmentWorkspaceRequest(normalized)");
  const ordinaryChat = appSource.indexOf("await runOrdinaryChat(prompt)");
  assert.ok(nativeHandler > -1);
  assert.ok(ordinaryChat > -1);
  assert.match(appSource, /Read or attach an assignment first/);
});

test("assignment analysis and workspace cards restore from persisted actions", () => {
  assert.match(clientSource, /\/chat\/assignments\/analyze/);
  assert.match(appSource, /createChatAssignmentAnalysis/);
  assert.match(appSource, /assignmentAnalysisFromActionPayload/);
  assert.match(appSource, /assignmentWorkspaceActionFromPayload/);
  assert.match(appSource, /continueConversation/);
});

test("restored workspace cards do not auto-run generation and keep duplicate-click lock", () => {
  const continueIndex = appSource.indexOf("async function continueConversation");
  const approveIndex = appSource.indexOf("async function approveWorkspaceAction");
  assert.ok(continueIndex > -1);
  assert.ok(approveIndex > -1);
  assert.equal(appSource.slice(continueIndex, approveIndex).includes("approveChatAssignmentWorkspace"), false);
  assert.match(appSource, /tryLockCommandAction\(locks\.current, lockId\)/);
  assert.match(appSource, /assignment-workspace:\$\{action\.actionId\}/);
});

test("awaiting folder card renders in the conversation with approval controls", () => {
  assert.match(appSource, /FolderAccessCard/);
  assert.match(appSource, /Folder access requested/);
  assert.match(appSource, /Approve read-only scan/);
  assert.match(appSource, /client\.approveChatFolder/);
  assert.match(appSource, /client\.cancelChatFolder/);
  assert.match(clientSource, /\/chat\/folders\/\$\{encodeURIComponent\(actionId\)\}\/approve/);
  assert.match(clientSource, /\/chat\/folders\/\$\{encodeURIComponent\(actionId\)\}\/cancel/);
});

test("action-only streams populate the existing assistant message", () => {
  assert.match(streamStateSource, /event\.event !== "action_required"/);
  assert.match(appSource, /actionRunFromStreamEvent\(event\)/);
  assert.match(appSource, /item\.id === assistantId/);
  assert.match(appSource, /folderAccessActionFromPayload\(actionRun\.action\)/);
});

test("folder actions restore from persisted run.action states", () => {
  assert.match(folderStateSource, /folderAccessActionFromPayload/);
  assert.match(folderStateSource, /action_type !== "folder_access"/);
  assert.match(appSource, /folderAccessActionFromPayload\(run\.action\)/);
  assert.match(appSource, /folderAction:/);
  assert.match(appSource, /Project folder connected/);
  assert.match(appSource, /Folder access cancelled\. No folder was scanned\./);
  assert.match(appSource, /Folder scan failed/);
});

test("project workflow remains chat-native without duplicating folder cards", () => {
  assert.match(appSource, /project_patch/);
  assert.match(appSource, /project_rollback/);
  assert.match(appSource, /ProjectSources/);
  assert.match(appSource, /genericActionFromRun/);
  assert.doesNotMatch(appSource, /ProjectManagementPage|PatchApprovalPage|ProjectEditorPage/);
});

test("restored folder cards do not auto-run scans and keep duplicate-click locks", () => {
  const continueIndex = appSource.indexOf("async function continueConversation");
  const approveIndex = appSource.indexOf("async function approveFolderAction");
  assert.ok(continueIndex > -1);
  assert.ok(approveIndex > -1);
  assert.equal(appSource.slice(continueIndex, approveIndex).includes("approveChatFolder"), false);
  assert.match(appSource, /folder-access:\$\{action\.actionId\}/);
  assert.match(appSource, /folder-rescan:\$\{action\.actionId\}/);
  assert.match(appSource, /tryLockCommandAction\(locks\.current, lockId\)/);
});

test("folder rescan updates the same card and inventory remains bounded", () => {
  assert.match(appSource, /client\.rescanChatFolder/);
  assert.match(clientSource, /\/chat\/folders\/\$\{encodeURIComponent\(actionId\)\}\/rescan/);
  assert.match(appSource, /updateFolderAction\(messageId/);
  assert.match(stylesSource, /\.folder-inventory-list\s*\{[^}]*max-height:\s*320px/s);
  assert.match(stylesSource, /\.folder-inventory-list\s*\{[^}]*overflow-y:\s*auto/s);
});

test("folder card avoids raw absolute scanned paths and keeps details collapsed", () => {
  assert.match(folderStateSource, /looksAbsolute/);
  assert.match(folderStateSource, /relative_path/);
  assert.doesNotMatch(appSource, /approved_root\}/);
  assert.doesNotMatch(appSource, /<details[^>]*\sopen/);
  assert.doesNotMatch(appSource, /open=\{/);
});
