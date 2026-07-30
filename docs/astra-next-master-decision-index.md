# Astra Next — Master Decision Index

Status: living index (update in the same commit as any freeze or amendment)  
Authority: Phase 1 charter overrides this index on wording conflicts; this index records *where* each freeze lives.  
Updated: 30 July 2026

---

## How to use

Each entry is a **frozen programme decision**. Changing it requires:

1. An explicit amendment in the owning charter or decision log;
2. A new row (or revision line) here;
3. A status bump in `docs/astra-next-master-status.json` when gates or sequence are affected.

IDs are stable. Do not renumber; supersede.

---

## Document hierarchy decisions

| ID | Decision | Source | Notes |
|---|---|---|---|
| D0.1 | Authority order: Phase 1 charter → master plan → phase charters → protocols → decision logs → probe artifacts → monograph | Master plan §0 | Monograph is explanatory only |
| D0.2 | Master plan does not redefine normative research terms | Master plan §0 | Sequencing and gates only |
| D0.3 | Implemented / emerging / proposed / research evidence-status discipline | Monograph v1.1 | Prevents presenting research as shipped |

---

## Phase 0 — Deterministic baseline

| ID | Decision | Source | Notes |
|---|---|---|---|
| D0.10 | Astra may operate without learned components | Baseline practice + monograph Ch 4 | Learning is additive, not required for safe operation |
| D0.11 | One model boundary (`LocalAIService`), one worker, one retrieval owner, one control plane | Stage docs + monograph | Track E preserves these invariants |
| D0.12 | phase0.v1 40/40 is the deterministic yardstick | Monograph snapshot / benchmark programme | Re-run and date results; do not weaken pass criteria silently |

---

## Phase 1 — Research identity (frozen)

| ID | Decision | Source | Notes |
|---|---|---|---|
| D1.1 | Capability object \(C=(A,P,I,V)\) | Phase 1 charter; monograph N.1 / Ch 7.2 | Identity = shared causal safety account, not source similarity |
| D1.2 | Canonical engineering episode is the experimental unit | Master plan §1; monograph experimental-unit note | Report episodes **and** independent repositories |
| D1.3 | Preserve observations; version interpretations; recompute derived state | Phase 1 charter epistemic principles | Supersession, never silent rewrite |
| D1.4 | Fixed authority kernel; learning cannot reinterpret it | Phase 1 charter | Safety, approvals, workspace, verification |
| D1.5 | Compiler is recommendation-only | Phase 1 charter; monograph authority matrix | No self-promotion |
| D1.6 | Preference quarantine; lowest authority; no compilation/export of preference records | Phase 1 charter | N=1 scope recording, not trait inference |
| D1.7 | Capability-relative transfer; held-out means uninvolved | Phase 1 charter; monograph Ch 11 | Stratum + changed-dimension signature |
| D1.8 | Repository-clustered evaluation | Master plan / monograph §11.7 correction | Episode count ≠ independent sample count |
| D1.9 | Capability count is not intelligence | Phase 1 charter; scientific test | Coverage and reliability reported separately |
| D1.10 | Feedback is observation; attribution is hypothesis | Phase 1 charter | Intent / plan / preference / context / interaction / presentation |
| D1.11 | Successful unification informs identity; failed bounded unification leaves identity unresolved | Phase 1 charter | Provisional split candidate at most |
| D1.12 | Applicability may parameterise a procedure; must not encode an alternate procedure | Phase 1 charter | Refine / split / compose model |
| D1.13 | Composite requires integration verifier beyond child conjunction; lifecycle ceiling of weakest dependency | Phase 1 charter | Probation propagates |
| D1.14 | Reopen Phase 1 only by explicit charter amendment | Phase 1 charter | No silent drift |

---

## Phase 2 — Knowledge representation

| ID | Decision | Source | Notes |
|---|---|---|---|
| D2.1 | Humans author grammars; Astra may discover compositions inside them | Phase 1 / Phase 2 position | Two-grammar requirement |
| D2.2 | Operations vs bindings are distinct | Phase 2 decision (to freeze in grammar charter) | Housekeeping: record canonical wording |
| D2.3 | Integration verification is required at graph level | Phase 2 probes | Children passing ≠ composition passing |
| D2.4 | Declared capability gaps are first-class | Phase 2 probes | FastAPI and pytest each have ≥1 declared gap |
| D2.5 | Fixture-identity features are forbidden | Phase 2 gate | 0 fixture-specific features in current probes |
| D2.6 | Phase 2 supports representation claims only | Master plan Phase 2 | Not discovery, transfer, or promotion |

---

## Phase 3 — Evidence architecture

| ID | Decision | Source | Notes |
|---|---|---|---|
| D3.1 | Observation / interpretation / assessment three-layer split | Phase 3 charter (in progress) | Maps to Phase 1 epistemic principle 7 |
| D3.2 | Outcome history attaches to immutable experiences, not permanently to capability versions | Phase 1 charter | Retroactive predicate membership |
| D3.3 | Preference records excluded from compilation and export | Phase 1 / Phase 3 | Quarantine store |
| D3.4 | Phase 3 gate = vocabulary v1→v2 recompute without mutating observations or rewriting v1 assessments | Master plan Phase 3 | Blocks Phase 6+ |

---

## Phases 4–15 — Scheduled freezes (placeholders)

These IDs are reserved. Fill when each phase charter freezes decisions.

| ID | Topic | Owning phase | Status |
|---|---|---|---|
| D4.1 | Trajectory equivalence and inclusion/exclusion | 4 | reserved |
| D4.2 | Canonical outcome taxonomy (incl. negatives) | 4 | reserved |
| D5.1 | Candidate capability / IR / dossier contracts | 5 | reserved |
| D5.2 | Compiler may request eval; never promote | 5 | reserved (reinforces D1.5) |
| D6.1 | Unresolved identity on failed unification | 6 | reserved (reinforces D1.11) |
| D7.1 | Applicability label set incl. abstention / invalid eval | 7 | reserved |
| D8.1 | Required simulation / mutation / replay reports | 8 | reserved |
| D9.1 | Transfer claim field set (stratum + signature + versions) | 9 | reserved |
| D10.1 | Lifecycle state machine and actor authority | 10 | reserved |
| D11.1 | Deployment mode ladder (research→shadow→opt-in→experimental→production) | 11 | reserved |
| D12.1 | Ranker allowlist-only; unsafe-selection hard zero | 12 | reserved |
| D13.1 | B0–B5 ladder and checkpoint episode set | 13 | reserved |
| D14.1 | Model-independence condition set | 14 | reserved |
| D15.1 | Capstone reproducibility checklist | 15 | reserved |

---

## Track E / Track R

| ID | Decision | Source | Notes |
|---|---|---|---|
| DE.1 | Engineering health is a parallel track; not a research result | Master plan §6 | Large-file / route consolidation |
| DR.1 | Isolated research packages do not import into production until promotion gate | Master plan / Track R | Fail closed |

---

## Amendment log

| Date | Change | Commit / note |
|---|---|---|
| 2026-07-30 | Initial master decision index created with Phase 0–3 freezes and reserved IDs for 4–15 | Master plan introduction |
