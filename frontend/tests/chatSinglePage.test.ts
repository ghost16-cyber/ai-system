import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const clientSource = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
const workspaceStateSource = readFileSync(new URL("../src/state/assignmentWorkspaceState.ts", import.meta.url), "utf8");
const folderStateSource = readFileSync(new URL("../src/state/folderAccessState.ts", import.meta.url), "utf8");
const streamStateSource = readFileSync(new URL("../src/state/chatStreamState.ts", import.meta.url), "utf8");
const projectJobStateSource = readFileSync(new URL("../src/state/projectJobState.ts", import.meta.url), "utf8");

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
  assert.match(stylesSource, /--composer-height:\s*128px/);
  assert.match(stylesSource, /--composer-clearance:\s*calc\(var\(--composer-height\) \+ 24px\)/);
  assert.match(stylesSource, /\.conversation\s*\{[^}]*padding-bottom:\s*var\(--composer-clearance\)/s);
  assert.match(stylesSource, /\.conversation\s*\{[^}]*scroll-padding-bottom:\s*var\(--composer-clearance\)/s);
  assert.match(stylesSource, /\.conversation-end\s*\{[^}]*scroll-margin-bottom:\s*var\(--composer-clearance\)/s);
  assert.match(stylesSource, /\.message\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/s);
  assert.match(stylesSource, /\.action-card, \.info-card\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/s);
  assert.match(stylesSource, /\.action-card, \.info-card\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s);
  assert.match(stylesSource, /\.action-card > \*, \.info-card > \*\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/s);
  assert.match(stylesSource, /\.action-card :where\(p, li, code\)\s*\{[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(stylesSource, /\.criterion > div, \.work-unit > div\s*\{[^}]*min-width:\s*0[^}]*flex:\s*1 1 auto/s);
  assert.match(stylesSource, /\.composer\s*\{[^}]*position:\s*fixed/s);
  assert.match(appSource, /new ResizeObserver\(update\)/);
  assert.match(appSource, /conversationEndRef\.current\?\.scrollIntoView/);
  assert.match(appSource, /const movedUp = currentTop < lastScrollTopRef\.current - 1/);
  assert.match(appSource, /if \(movedUp\) stickToBottomRef\.current = false/);
  assert.match(appSource, /pendingScrollRestoreRef\.current = readScrollSnapshot/);
  assert.match(appSource, /conversationId && !scrollRestoreConversation/);
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

test("project jobs remain integrated in the single chat with independent approvals", () => {
  assert.match(appSource, /ProjectJobCard/);
  assert.match(appSource, /Prepare patch preview/);
  assert.match(appSource, /Propose validation/);
  assert.match(appSource, /Clarification needed/);
  assert.match(appSource, /Client-ready completion/);
  assert.match(appSource, /project-job-prepare:\$\{job\.jobId\}/);
  assert.match(appSource, /project-job-validation:\$\{job\.jobId\}/);
  assert.match(clientSource, /\/chat\/projects\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/prepare/);
  assert.match(clientSource, /\/chat\/projects\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/validation/);
  assert.doesNotMatch(appSource, /ProjectJobPage|JobDashboard|ExecutionConsole/);
});

test("project job cards deduplicate stream and reload state by job id", () => {
  assert.match(appSource, /mergeProjectJobRun\(current, actionRun, assistantId\)/);
  assert.match(appSource, /latestJobs = new Map<string, ProjectJobAction>/);
  assert.match(appSource, /renderedJobs = new Set<string>/);
  assert.match(projectJobStateSource, /action\.action_type !== "project_job"/);
  assert.match(projectJobStateSource, /normalized\.startsWith\("\/"\)/);
});

test("Stage 6 structural analysis stays bounded inside the existing job card", () => {
  assert.match(appSource, /Analyzed project structure/);
  assert.match(appSource, /Relevant symbols/);
  assert.match(appSource, /Coherent file set/);
  assert.match(appSource, /Impacted tests/);
  assert.match(appSource, /Pre-preview validation/);
  assert.match(appSource, /Plan-only safety stop/);
  assert.match(projectJobStateSource, /safePath\(readString\(item\.relative_path\)\)/);
  assert.match(clientSource, /project_analysis_completed/);
  assert.match(clientSource, /project_impact_ready/);
  assert.match(clientSource, /\/analysis\/refresh/);
  assert.doesNotMatch(appSource, /StructuralAnalysisPage|DependencyGraphPage|CodeEditor/);
});

test("the active conversation is restored automatically after a page reload", () => {
  assert.match(appSource, /ACTIVE_CONVERSATION_KEY/);
  assert.match(appSource, /localStorage\.setItem\(ACTIVE_CONVERSATION_KEY, conversationId\)/);
  assert.match(appSource, /client\.getChatConversation\(selectedConversationId\)/);
  assert.match(appSource, /restoreConversationMessages\(detail/);
  assert.match(appSource, /canonicalConversationTurns\(detail\.turns\)/);
  assert.match(appSource, /hydrationStatus === "loading"/);
  assert.match(appSource, /localStorage\.removeItem\(ACTIVE_CONVERSATION_KEY\)/);
});

test("reload recovery never resends a prompt or trusts browser project state", () => {
  const hydrateStart = appSource.indexOf("async function hydrateConversation");
  const submitStart = appSource.indexOf("async function submit");
  assert.ok(hydrateStart > -1 && submitStart > hydrateStart);
  const hydrationSource = appSource.slice(hydrateStart, submitStart);
  assert.doesNotMatch(hydrationSource, /streamChat|runChat|approveProjectDelivery|prepareProjectDelivery/);
  assert.match(appSource, /detail\.project_deliveries/);
  assert.match(appSource, /client\.createChatRequest/);
  assert.match(appSource, /request_id:\s*pendingRequest\.request_id/);
  assert.match(clientSource, /postJson<ChatRequestRecord>\("\/chat\/requests"/);
  assert.match(appSource, /clearStreamRecovery\(sessionStorage\)/);
  assert.match(appSource, /It was not replayed automatically/);
});

test("New chat clears transient authority and scroll state without deleting history", () => {
  const newChatStart = appSource.indexOf("function newChat");
  const continueStart = appSource.indexOf("async function continueConversation");
  const source = appSource.slice(newChatStart, continueStart);
  assert.match(source, /clearStreamRecovery\(sessionStorage\)/);
  assert.match(source, /clearScrollSnapshot\(sessionStorage, conversationId\)/);
  assert.match(source, /setMessages\(\[\]\)/);
  assert.doesNotMatch(source, /deleteChat|\/chat\/conversations/);
});

test("project job layout preserves the existing mobile composer", () => {
  assert.match(stylesSource, /\.project-job-card\s*\{/);
  assert.match(stylesSource, /\.job-columns\s*\{[^}]*grid-template-columns:\s*repeat\(2/s);
  assert.match(stylesSource, /@media \(max-width: 640px\)[\s\S]*\.job-columns\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s);
  assert.match(stylesSource, /\.job-section\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)[^}]*min-width:\s*0/s);
  assert.match(stylesSource, /\.completion-report dl div\s*\{/);
  assert.match(stylesSource, /@media \(max-width: 640px\)[\s\S]*\.analysis-heading\s*\{[^}]*flex-direction:\s*column/s);
});
