# Astra Next Master Programme Plan

Status: **active operational decomposition of the frozen seven-phase roadmap**  
Normative phase numbering: Phase 1 Research Charter §0.1  
Companion monograph: `output/pdf/Astra_Next_Research_Blueprint_v1.1.pdf` (explanatory)  
Machine status: `docs/astra-next-master-status.json`  
Decision index: `docs/astra-next-master-decision-index.md`  
Artifact catalogue: `artifacts/research/master/artifact-index.json`  
Updated: 30 July 2026

---

## 0. Role of this document

This master programme plan is the **operational decomposition** of the frozen seven-phase roadmap in the Phase 1 Research Charter. It does not replace the charter.

Its job is to show:

- where Astra is now;
- what is frozen;
- what remains experimental;
- work-package dependencies under each **charter phase**;
- the completion gate for every work package;
- which claims each work package is allowed to support;
- what must not be built early.

It matches the research monograph’s separation between **implemented / emerging / proposed / research** work, and the requirement that learned components remain subordinate to the fixed safety kernel.

### Normative phase numbering (charter — do not rename)

| Charter phase | Official name |
|---|---|
| 0 | Boundaries |
| 1 | Identity |
| 2 | Knowledge |
| 3 | Evidence |
| 4 | Capability Compiler |
| 5 | Algorithms |
| 6 | Purposeful Data Collection |
| 7 | Integration & Longitudinal Evaluation |

Detailed units in this plan are **work packages** (e.g. `3A`, `4C`, `7A`), nested under these seven phases. They are **not** a second phase-numbering system.

### Document hierarchy

| # | Artifact | Role |
|---|---|---|
| 1 | **Phase 1 Research Charter** | Normative research constitution (definitions, authority, evidence, claims) |
| 2 | **Master Programme Plan** (this document) | Operational decomposition of the charter’s seven phases; may not rename them |
| 3 | **Phase charters** | Normative phase-local decisions |
| 4 | **Pre-registered protocols** | Experimental procedures fixed before relevant implementation |
| 5 | **Decision logs** | Amendments and frozen interpretations |
| 6 | **Probe artifacts** | Machine-readable evidence |
| 7 | **Monograph** | Explanatory synthesis |

**Coordination rule.** The master plan coordinates and sequences phase work. It outranks phase documents only on programme status and dependency ordering where no higher charter rule applies. It may **not** override the Phase 1 charter or silently replace normative phase-local decisions. The Phase 1 charter overrides less precise monograph language.

---

## 1. Programme thesis

> Astra compiles verified procedural capabilities from experience.

The model is temporary reasoning support. Authority, evidence, execution, verification, and accumulated procedural intelligence belong to Astra.

**Canonical engineering episode** (charter term — use exactly):

> One immutable request-to-outcome record containing the pre-decision evidence package, available alternatives, intervention, execution, verification, and final classified outcome.

The words *project*, *task*, *attempt*, *work unit*, and *episode* are **not** interchangeable. Longitudinal checkpoints count **canonical engineering episodes** and separately report **repositories** and **repository-lineage clusters**.

Do **not** define “project count” as episode count. Retire “project count” from experimental reporting.

Improvement is measured over memory-only baselines (B2), under fixed resources and a fixed safety kernel, separately for within-repository adaptation and cross-repository transfer, plus survival under model replacement or removal (B5).

---

## 2. Evidence-status labels

When stating current status, every evidence item carries one of:

| Evidence class | Meaning |
|---|---|
| **Repository verified** | Inspected code, artifact, and test outputs in this repository |
| **Operator reported** | Report supplied; underlying files not independently inspected in this review |
| **Proposed** | Planned deliverable |
| **Research hypothesis** | Requires experiment |

Upgrade Phase 2 from operator-reported to repository-verified only after the artifact catalogue binds grammar probe hashes and test records.

---

## 3. Where Astra is now

| Charter phase | Work packages | Status | Evidence class |
|---|---|---|---|
| 0 Boundaries | 0A deterministic baseline; 0B engineering health | 0A complete; 0B ongoing | Repository verified (lifecycle, LocalAI boundary, isolation docs); benchmark 40/40 cited in monograph |
| 1 Identity | Frozen charter | Frozen | Operator reported / external until charter file is hash-bound in-repo |
| 2 Knowledge | 2A–2D grammar foundation | Complete; closure pending | **Operator reported** probes (FastAPI / pytest tables below) |
| 3 Evidence | 3A architecture; 3B recomputation | **In progress** | Proposed / in implementation |
| 3 Evidence | 3C trajectory normalization; 3D dataset quality | Blocked | Proposed |
| 4 Capability Compiler | 4A–4F | Blocked until 7A + 3 gate | Proposed |
| 5 Algorithms | 5A–5D | Emerging baseline only (shadow) | Repository verified advisory spine ≠ compiler |
| 6 Purposeful Data Collection | 6A–6E | Not started | Proposed |
| 7 Integration & Longitudinal | 7A pre-registration | **Must occur before 4A** | Proposed (next conceptual task) |
| 7 Integration & Longitudinal | 7B–7G | Not started | Research hypothesis |

The monograph is explicit: the current decision/outcome spine is **not** a capability compiler.

### Phase 2 probe summary (operator reported)

| Grammar | Ops | Evidence features | Invariants | Verifiers / contracts | Fixtures | Distinct graphs | Declared gaps | Fixture-specific features |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FastAPI | 7 | 8 | 6 | 4 | 3 | 3 | 1 | 0 |
| Pytest | 6 (+1 typed binding) | 6 | 4 | 3 | 3 | 3 | 1 | 0 |

**Supported claim (once repository-verified):** two structurally distinct bounded grammars can represent multiple evidence-conditioned procedures without fixture identity or unrestricted generation.  
**Not yet supported:** automatic discovery; learned applicability; transfer; promotion; capability growth.

---

## 4. What is frozen vs experimental

### Frozen (charter amendment required)

- Safety / authority / approvals / workspace / verification kernel
- Capability object \(C = (A, P, I, V)\)
- Immutable observations; versioned interpretations; recomputable derived assessments
- Compiler recommendation-only authority
- Preference quarantine
- Capability-relative transfer; held-out means uninvolved
- Repository-clustered evaluation
- “Capability count is not intelligence”
- Official seven-phase numbering (§0.1)
- **Phase 7 pre-registration before Phase 4 compiler implementation**

### Experimental (fail-closed / shadow until gates pass)

- Pattern mining and anti-unification
- Learned applicability and strategy ranking
- Capability promotion and production execution
- Longitudinal growth claims (B0–B5)
- Model-independence claims
- Production imports from research packages

---

## 5. Critical pre-registration rule (charter)

> **Before Charter Phase 4A begins, pre-register the Charter Phase 7 evaluation protocol**, including baseline definitions, repository and lineage partitioning, checkpoints, metrics, clustering method, held-out contamination rules, and falsification criteria. Amendments after compiler implementation must be versioned, justified, and reported as protocol deviations.

This is stronger than “before confirmatory claims.” The protocol must be frozen **before compiler implementation begins**, so implementation cannot adapt to an unstated evaluation target.

Codex is currently on Charter Phase 3; timing remains recoverable. **7A is the next conceptual task before 4A.**

---

## 6. Dependency chain (charter phases + work packages)

```text
Phase 0  Boundaries / baseline
    ↓
Phase 1  Identity (frozen)
    ↓
Phase 2  Knowledge (grammars)
    ↓
Phase 3  Evidence (3A → 3B gate → 3C/3D preparation)
    ↓
Phase 7A Pre-register longitudinal protocol   ←── required before 4A
    ↓
Phase 4  Capability Compiler (4A → … → 4F)
    ↓
Phase 5  Algorithms (shadow → gated promote)
    ↓
Phase 6  Purposeful Data Collection (only for pre-registered experiments)
    ↓
Phase 7  Integration & Longitudinal Evaluation (7B–7G)
```

Phase 6 may run **in parallel with late Phase 4 / Phase 5** only for datasets named in the Phase 7A protocol. It must not become an open-ended data grab driven by the compiler.

### Must not be built early

| Forbidden early step | Blocked until |
|---|---|
| 4A manual compiler contracts | Phase 3 gate **and** 7A pre-registration |
| 4C+ miner / discovery | 3B recomputation gate + 4A/4B contracts + normalized negatives (3C) |
| Learned applicability (4E) | Normalized positive **and** negative examples |
| Production promotion | Final promotion evidence + research path complete; see §8 |
| Intelligence-growth claim | 7E chronological B0–B5 |
| Model-independence claim | 7F model swap/removal |
| Bulk “data collection” under Phase 3 | Reserved for Phase 6; Phase 3 only prepares existing evidence |

---

## 7. Work-package programme (nested under charter phases)

### Charter Phase 0 — Boundaries and deterministic baseline

#### 0A — Baseline preservation

**Purpose.** Establish that Astra already has a functioning, bounded engineering system before introducing learning.

**Evidence (repository verified / monograph-cited).** Canonical project lifecycle; approvals and idempotency; bounded Docker validation; `LocalAIService`; deterministic semantic edits; retrieval; worker/frontend flow; deterministic benchmark programme (40/40 phase0.v1 cited in monograph).

**Gate.** Reproducible static baseline with exact artifacts, tests, hardware configuration; no learned components required for operation.

**Claims allowed.** Bounded deterministic engineering under a fixed safety kernel.  
**Claims forbidden.** Capability growth, transfer, model independence, learned intelligence.

#### 0B — Engineering health and regression control (Track E)

**Purpose.** Separate complexity debt from research experiments.

**Work.** Reduce concentration in large entrypoints; consolidate legacy vs canonical routes; preserve one worker, one model boundary, one retrieval owner; full-suite regression; WSL/Docker tooling.

**Rule.** Ordinary refactoring must not masquerade as research improvement.

---

### Charter Phase 1 — Identity

**Status.** Complete and frozen.

**Reopening.** Explicit charter amendment only.

**Gate.** Frozen charter with amendment log; monograph subordinate on definitions.

---

### Charter Phase 2 — Knowledge

| WP | Name | Status |
|---|---|---|
| 2A | Grammar contracts | Complete (closure pending) |
| 2B | FastAPI operationalisation | Complete (operator reported) |
| 2C | pytest operationalisation | Complete (operator reported) |
| 2D | Grammar versioning and artifact closure | Remaining housekeeping |

**Housekeeping (2D).** Patch-level grammar versions; freeze operation-versus-binding in the decision index; record granularity pressure points; hash-bind all Phase 2 artifacts.

**Gate.** Two grammars, each with multiple valid graphs, mechanistic witnesses, independent integration verification, declared gaps, and no fixture-identity features — **repository verified**.

**Supported claim after gate.** Representation without fixture identity or unrestricted generation.  
**Not supported.** Discovery, learned applicability, transfer, promotion, capability growth.

---

### Charter Phase 3 — Evidence

**Core architecture.**

```text
Observation layer      → immutable source and event references
Interpretation layer   → versioned extractor outputs
Assessment layer       → recomputable derived claims
```

| WP | Name | Status | Role |
|---|---|---|---|
| **3A** | Immutable evidence architecture | In progress | Schemas, stores, episode identity |
| **3B** | Vocabulary recomputation probe | In progress | Scientific gate for Phase 3 |
| **3C** | Trajectory normalization | Blocked | **Evidence preparation** — normalize existing fixture/canonical episode evidence |
| **3D** | Dataset quality and lineage partitioning | Blocked | Splits, taxonomy, quality audit over **prepared** evidence |

**Distinction (critical).**  
- **Phase 3** converts *existing* fixture and canonical episode evidence into normalized, recomputable records.  
- **Phase 6** obtains *additional* repositories, episodes, seeded variants, adversarial cases, and transfer targets required by the **pre-registered** Phase 7 protocol.

Trajectory normalization must not quietly become bulk data collection.

**3B scientific question.** Can Astra recompute a derived claim under a new evidence vocabulary while preserving original observations and the historical assessment unchanged?

**3B gate.** Unchanged observation hashes; new interpretations under vocabulary v2; superseded but unmodified v1 assessment; explicit changed result; no held-out leakage; no preference contamination; no episode fragmentation.

**3C/3D gate.** Every trajectory links to immutable observations, exact vocabulary versions, and a canonical outcome taxonomy (including failures, rejections, abstentions, rollbacks). Success-only datasets are forbidden.

---

### Charter Phase 4 — Capability Compiler

**Blocked until:** Phase 3 gate **and** work package **7A** complete.

| WP | Name | Notes |
|---|---|---|
| **4A** | Manual compiler baseline / contracts | Interfaces before discovery |
| **4B** | Candidate IR and dossiers | Research artifacts only |
| **4C** | Pattern mining | After 4A/4B + normalized negatives |
| **4D** | Bounded anti-unification | Failed search → unresolved / provisional split only |
| **4E** | Applicability inference | Must not hide alternate procedures in predicates |
| **4F** | Simulation, mutation, historical replay | Required before held-out transfer claims |

**Restriction.** Candidates remain research artifacts. The compiler may propose and **request** evaluation; it cannot promote or mutate lifecycle state.

**4A gate.** A human-authored candidate passes the compiler pipeline without production activation.

**4C–4D gate.** Candidates outperform exact-template recovery on held-out trace reconstruction while preserving type, invariant, and verifier compatibility.

**4E gate.** Improves activation precision or coverage without increasing unsafe activation or encoding alternate procedures in the predicate.

**4F gate.** Every candidate has simulation, mutation, and historical replay reports; known failure boundary; explicit unresolved cases.

---

### Charter Phase 5 — Algorithms

| WP | Name | Status |
|---|---|---|
| **5A** | Strategy ranker | Emerging / shadow only |
| **5B** | Failure predictor | Not started |
| **5C** | Memory utility | Emerging baseline |
| **5D** | Calibration and shadow evaluation | Required before any promote |

**Gate.** Ranker (or related models) improves selection efficiency or success while unsafe-selection rate remains zero; deterministic fallback remains available; allowlist only — no new strategies invented by the learner.

---

### Charter Phase 6 — Purposeful Data Collection

Collect **only** what pre-registered experiments require.

| WP | Name |
|---|---|
| **6A** | Defined experimental datasets only |
| **6B** | Natural repositories |
| **6C** | Seeded factorial variants |
| **6D** | Negative and adversarial cases |
| **6E** | Held-out repository reservations |

**Gate.** Every collected artifact cites the Phase 7A protocol experiment that requires it; held-out targets are reserved before influencing vocabulary, verifiers, predicates, or thresholds.

---

### Charter Phase 7 — Integration and Longitudinal Evaluation

| WP | Name | Status |
|---|---|---|
| **7A** | Pre-registration | **Must complete before 4A** |
| **7B** | Held-out transfer evaluation | Not started |
| **7C** | Capability governance (research vs production) | Not started |
| **7D** | Authorized shadow execution | Not started |
| **7E** | Chronological B0–B5 experiment | Not started |
| **7F** | Model replacement / removal | Not started |
| **7G** | Capstone consolidation and reproduction | Not started |

#### 7A — Pre-registration contents

Baseline definitions (B0–B5); repository and lineage partitioning; checkpoints; metrics (coverage, reliability, abstention, clustering); held-out contamination rules; falsification criteria; amendment policy for protocol deviations.

#### 7B — Transfer

Capability-relative reporting: capability version; source/target profiles; evidence-vocabulary version; changed-dimension signature; stratum; applicability / procedure / invariant / verification results; repository lineage.

Outcomes: transfer success; correct abstention; false applicability; procedural / invariant / verification failure; invalid evaluation.

#### 7C / 7D — Governance and execution (two ladders)

**Research governance** may permit: candidate; probationary; replay-verified; experimental; **research shadow execution**.

**Production governance** may permit: production; normal strategy selection; user-facing automatic candidacy.

Production state requires the final promotion evidence defined in the charter. The compiler remains recommendation-only in both ladders. Shadow/research execution before the longitudinal experiment must **not** imply production promotion before the core hypothesis is tested.

#### 7E — Baseline ladder

| ID | System | Purpose |
|---|---|---|
| B0 | Static deterministic | Non-neural competence |
| B1 | Static + SLM | Model contribution |
| B2 | Memory only | Separates recall from learning (must be strong) |
| B3 | Strategy ranker | Policy learning among fixed capabilities |
| B3.5 | Prompt-guidance control | Same procedures as text vs deterministic execution |
| B4 | Capability compiler | Central contribution |
| B5 | Model swap / removal | Model independence |

**Checkpoints.** 0, 100, 250, 500, 750, 1000 **canonical engineering episodes** — also report independent repositories, lineage clusters, task-family distribution, coverage, reliability, correct abstention, false applicability, model calls, generated characters, GPU time, human burden, regression cost, library maintenance cost.

**7E gate.** Compiled capabilities outperform static, memory-only, and ranker-only baselines on repository-disjoint held-out tasks without weakening safety or merely narrowing coverage.

#### 7F / 7G

**7F gate.** Previously compiled capabilities retain meaningful utility after model replacement or removal.  
**7G gate.** Another person can reproduce principal results from repository, frozen configuration, and documented commands.

---

## 8. Master status dashboard

| Charter phase | Work package | Status |
|---|---|---|
| 0 | 0A deterministic baseline | Complete |
| 0 | 0B engineering health | Ongoing |
| 1 | Research identity | Frozen |
| 2 | 2A–2D grammar foundation | Complete; closure pending |
| 3 | 3A evidence architecture | In progress |
| 3 | 3B recomputation probe | In progress |
| 3 | 3C trajectory normalization | Blocked |
| 3 | 3D dataset quality / lineage | Blocked |
| 4 | 4A manual compiler baseline | Blocked (needs 3 gate + **7A**) |
| 4 | 4B–4F discovery and replay | Blocked |
| 5 | 5A–5D decision algorithms | Emerging baseline only |
| 6 | 6A–6E purposeful collection | Not started |
| 7 | **7A pre-registration** | **Must occur before 4A** |
| 7 | 7B–7G integration and evaluation | Not started |

Authoritative machine copy: `docs/astra-next-master-status.json`.

---

## 9. Implementation, test, and experiment map

| WP | Implement | Test | Experiment |
|---|---|---|---|
| 0A | Maintain deterministic core | Regression + phase0.v1 | Baseline only |
| 0B | Complexity reduction | Full-suite health | None (not research) |
| 2D | Hash-bind grammars | Lint + fixture traces | Manual representation probe |
| 3A/3B | Episode store, extractors, assessments | Recomputation, leakage, preference quarantine | Vocabulary v1→v2 |
| 3C/3D | Trajectory normalizer over existing evidence | Schema round-trip, taxonomy | Dataset quality audit |
| 7A | Protocol document | Review checklist | Freeze before 4A |
| 4A/4B | Compiler IR + dossier + request APIs | Manual candidate pipeline | No discovery yet |
| 4C/4D | Miner + anti-unification | Type/invariant/verifier | vs exact template / NN |
| 4E | Applicability models | Calibration, abstention | vs static predicates |
| 4F | Mutation + replay harness | Boundary/drift suites | Candidate robustness |
| 5A–5D | Rankers / predictors | Unsafe-selection = 0 | Shadow vs rules |
| 6A–6E | Collection only per protocol | Lineage / reservation audits | Named experiments only |
| 7B–7G | Transfer, governance, chronos, model swap, capstone | Leakage + clustering | B0–B5 / model independence |

---

## 10. Artifact identity convention

Use charter phase + work package in paths and IDs:

| Example ID | Meaning |
|---|---|
| `phase3a-evidence-architecture` | 3A schemas/store |
| `phase3b-recomputation-probe` | 3B gate probe |
| `phase3c-trajectory-normalization` | 3C preparation |
| `phase4a-manual-compiler` | 4A contracts |
| `phase4c-pattern-mining` | 4C discovery |
| `phase7a-preregistered-protocol` | 7A protocol freeze |

Do not invent a parallel `phase13-…` style numbering.

---

## 11. Immediate next actions

1. **Finish 3A/3B** — evidence recomputation probe (observation hashes stable; v1 assessment superseded, not rewritten).
2. **Draft 7A** — pre-register the Phase 7 evaluation protocol **before** any 4A compiler contract work.
3. **2D housekeeping** — hash-bind grammar probes; upgrade Phase 2 evidence class when bound.
4. **0B Track E** — continue complexity reduction on a separate track.
5. **Do not start 4A+** until 3B gate **and** 7A pass.

---

## 12. Amendment policy

- Changes to **definitions / authority / evidence rules / official phase names or numbers** → Phase 1 charter amendment + decision-index entry.
- Changes to **work-package sequence, gates, or status** → update this plan + `astra-next-master-status.json` in the same commit; must not rename charter phases.
- Changes to **phase-local rules** → that phase’s charter/protocol; indexed here.
- Monograph updates are explanatory; they never silently override the charter or this plan’s gates.
