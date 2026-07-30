# Astra Next — Master Decision Index

Status: living index (update in the same commit as any freeze or amendment)  
Authority: Phase 1 charter overrides this index on wording conflicts; this index records *where* each freeze lives.  
Normative phase numbering: Phase 1 Research Charter §0.1  
Updated: 30 July 2026 (revision: retire rival 0–15 phase taxonomy)

---

## How to use

Each entry is a **frozen programme decision**. Changing it requires:

1. An explicit amendment in the owning charter or decision log;
2. A new row (or revision line) here;
3. A status bump in `docs/astra-next-master-status.json` when gates or sequence are affected.

IDs are stable. Do not renumber; supersede.  
Work packages use IDs like `3A`, `4C`, `7A` — never a second phase number.

---

## Document hierarchy and numbering

| ID | Decision | Source | Notes |
|---|---|---|---|
| D0.1 | Authority: Phase 1 charter → master programme plan → phase charters → protocols → decision logs → probe artifacts → monograph | Master plan §0 | Monograph is explanatory only |
| D0.2 | Master plan is operational decomposition of the charter’s **seven** phases; it may not rename them | Master plan §0; this revision | Work packages nest under charter phases |
| D0.3 | Master plan outranks phase docs only on programme status/dependency ordering where no higher charter rule applies | Master plan §0 | Cannot override phase-local substance |
| D0.4 | Official phase set: 0 Boundaries … 7 Integration & Longitudinal Evaluation | Charter §0.1 | Normative |
| D0.5 | Implemented / emerging / proposed / research evidence-status discipline | Monograph v1.1 | Plus operator-reported / repository-verified labels in master plan |
| D0.6 | Superseded: master-plan phases numbered 0–15 as primary identifiers | This revision | Replaced by charter phases + work packages |

---

## Phase 0 — Boundaries

| ID | Decision | Source | Notes |
|---|---|---|---|
| D0.10 | Astra may operate without learned components | Baseline + monograph | Learning is additive |
| D0.11 | One model boundary, one worker, one retrieval owner, one control plane | Stage docs + monograph | Preserved by work package 0B |
| D0.12 | phase0.v1 deterministic benchmark is the yardstick | Monograph / benchmark programme | Re-run and date; do not weaken criteria silently |
| D0.13 | Track E (0B) must not be reported as research improvement | Master plan | Parallel engineering health |

---

## Phase 1 — Identity (frozen)

| ID | Decision | Source | Notes |
|---|---|---|---|
| D1.1 | Capability object \(C=(A,P,I,V)\) | Phase 1 charter | Identity = shared causal safety account |
| D1.2 | Experimental unit term is **canonical engineering episode** | Phase 1 charter; master plan §1 | Exact wording; retire “project count” from reporting |
| D1.3 | Preserve observations; version interpretations; recompute derived state | Phase 1 charter | Supersession, never silent rewrite |
| D1.4 | Fixed authority kernel | Phase 1 charter | Safety, approvals, workspace, verification |
| D1.5 | Compiler is recommendation-only | Phase 1 charter | No self-promotion |
| D1.6 | Preference quarantine; lowest authority; excluded from compilation/export | Phase 1 charter | N=1 scope recording |
| D1.7 | Capability-relative transfer; held-out means uninvolved | Phase 1 charter | Stratum + changed-dimension signature |
| D1.8 | Repository-clustered evaluation | Charter / monograph §11.7 | Episode count ≠ independent sample count |
| D1.9 | Capability count is not intelligence | Phase 1 charter | Coverage and reliability separate |
| D1.10 | Feedback is observation; attribution is hypothesis | Phase 1 charter | Six latent causes |
| D1.11 | Successful unification informs identity; failed bounded unification leaves identity unresolved | Phase 1 charter | Provisional split at most |
| D1.12 | Applicability may parameterise; must not encode an alternate procedure | Phase 1 charter | Refine / split / compose |
| D1.13 | Composite requires integration verifier; lifecycle ceiling of weakest dependency | Phase 1 charter | Probation propagates |
| D1.14 | Reopen Phase 1 only by explicit charter amendment | Phase 1 charter | No silent drift |
| D1.15 | **Phase 7 must be pre-registered before Phase 4 builds the compiler** | Charter §0.1 | Operationalized as: 7A before 4A |

---

## Phase 2 — Knowledge

| ID | Decision | Source | Notes |
|---|---|---|---|
| D2.1 | Humans author grammars; Astra may discover compositions inside them | Charter / Phase 2 | Two-grammar requirement |
| D2.2 | Operations vs bindings are distinct | Phase 2 (freeze in 2D) | Housekeeping |
| D2.3 | Integration verification required at graph level | Phase 2 probes | Children passing ≠ composition |
| D2.4 | Declared capability gaps are first-class | Phase 2 probes | |
| D2.5 | Fixture-identity features are forbidden | Phase 2 gate | |
| D2.6 | Phase 2 supports representation claims only | Master plan | Not discovery/transfer/promotion |
| D2.7 | Current FastAPI/pytest probe tables are **operator reported** until hash-bound | Master plan §2 | Upgrade evidence class after 2D |

---

## Phase 3 — Evidence

| ID | Decision | Source | Notes |
|---|---|---|---|
| D3.1 | Observation / interpretation / assessment three-layer split | Phase 3 | Maps to epistemic principle 7 |
| D3.2 | Outcome history attaches to immutable experiences | Phase 1 / 3 | Retroactive predicate membership |
| D3.3 | Preference records excluded from compilation and export | Phase 1 / 3 | |
| D3.4 | 3B gate = vocabulary v1→v2 recompute without mutating observations or rewriting v1 assessments | Master plan | Blocks 4A with 7A |
| D3.5 | 3C/3D are **evidence preparation** of existing episodes/fixtures — not purposeful bulk collection | Master plan | Collection belongs in Phase 6 |

---

## Phase 4 — Capability Compiler (work packages)

| ID | Decision | Source | Notes |
|---|---|---|---|
| D4.1 | 4A blocked until 3B gate **and** 7A | Charter D1.15 + master plan | |
| D4.2 | Candidates are research artifacts; compiler requests eval only | Charter / 4A | Reinforces D1.5 |
| D4.3 | Failed bounded unification → unresolved / provisional split only | Charter / 4D | Reinforces D1.11 |
| D4.4 | Reserved: IR, dossier, witness, batch identity contracts | 4A/4B | Fill when frozen |

---

## Phase 5 — Algorithms

| ID | Decision | Source | Notes |
|---|---|---|---|
| D5.1 | Ranker allowlist-only; unsafe-selection hard zero | 5A/5D | Shadow before promote |
| D5.2 | Reserved: failure predictor / memory utility promote criteria | 5B/5C | |

---

## Phase 6 — Purposeful Data Collection

| ID | Decision | Source | Notes |
|---|---|---|---|
| D6.1 | Collect only datasets named by the Phase 7A protocol | Charter Phase 6 | No open-ended grab |
| D6.2 | Held-out targets reserved before influencing vocabulary/verifiers/predicates/thresholds | 6E / charter | |
| D6.3 | Phase 6 is distinct from Phase 3 preparation | Master plan | Prevents 3C scope creep |

---

## Phase 7 — Integration & Longitudinal Evaluation

| ID | Decision | Source | Notes |
|---|---|---|---|
| D7.1 | 7A protocol contents: baselines, partitioning, checkpoints, metrics, clustering, contamination, falsification, deviation policy | Master plan 7A | Before 4A |
| D7.2 | Research governance ≠ production governance | Master plan §7C | Shadow ≠ production |
| D7.3 | B0–B5 ladder including B3.5 | Charter / monograph | |
| D7.4 | Checkpoints count canonical engineering episodes; report repos and lineage separately | D1.2 | |
| D7.5 | Reserved: transfer claim field set; capstone reproducibility checklist | 7B/7G | |

---

## Track R

| ID | Decision | Source | Notes |
|---|---|---|---|
| DR.1 | Isolated research packages do not import into production until promotion gate | Master plan | Fail closed |

---

## Amendment log

| Date | Change | Note |
|---|---|---|
| 2026-07-30 | Initial master decision index | Introduced with provisional 0–15 plan |
| 2026-07-30 | **Revision:** align to charter seven-phase numbering; work packages; 7A-before-4A; canonical engineering episode wording; Phase 3 vs 6 split; research vs production governance; evidence-status labels; D0.6 supersession of 0–15 taxonomy | Governance defect correction |
