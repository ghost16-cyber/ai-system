import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const card = readFileSync(new URL("../src/components/ProjectControlCard.tsx", import.meta.url), "utf8");

test("Phase 7 evidence remains advisory and contains no action controls", () => {
  assert.match(card, /Cited repository context/);
  assert.match(card, /untrusted reference material/);
  assert.match(card, /advisory only/);
  const evidenceSection = card.slice(card.indexOf("rag-evidence-card"), card.indexOf("manualCriteria.map"));
  assert.doesNotMatch(evidenceSection, /onAction/);
  assert.doesNotMatch(evidenceSection, /approve/i);
  assert.doesNotMatch(evidenceSection, /execute/i);
});

test("citations expose accessible bounded metadata and reload-owned artifact state", () => {
  assert.match(card, /aria-label="Advisory retrieval evidence"/);
  assert.match(card, /citation\.relative_path/);
  assert.match(card, /citation\.line_start/);
  assert.match(card, /citation\.line_end/);
  assert.match(card, /citation\.excerpt/);
  assert.match(card, /artifact\.invalidated/);
  assert.match(card, /<details className="technical rag-citation"/);
});
