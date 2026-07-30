# Astra Next Master Plan

Status: **active programme plan** (30 July 2026)  
Normative charter: Phase 1 Research Charter (frozen)  
Companion monograph: `output/pdf/Astra_Next_Research_Blueprint_v1.1.pdf`  
Machine status: `docs/astra-next-master-status.json`  
Decision index: `docs/astra-next-master-decision-index.md`  
Artifact catalogue: `artifacts/research/master/artifact-index.json`

---

## 0. Role of this document

The master plan sits **above** the phase charters. It does not replace them.

Its job is to show:

- where Astra is now;
- what is frozen;
- what remains experimental;
- the dependency order;
- the completion gate for every phase;
- which claims each phase is allowed to support;
- what must not be built early.

It matches the research monograph’s separation between **implemented / emerging / proposed / research** work, and the requirement that learned components remain subordinate to the fixed safety kernel.

### Document hierarchy (authority order)

| # | Artifact | Role |
|---|---|---|
| 1 | **Phase 1 Research Charter** | Normative definitions, authority boundaries, evidence rules, research claims |
| 2 | **Astra Next Master Plan** (this document) | Programme sequencing, dependencies, gates, deliverables, current status |
| 3 | **Phase Charters** | Normative rules for each individual phase |
| 4 | **Phase Protocols** | Pre-registered implementation and experiment procedures |
| 5 | **Decision Logs** | Frozen conclusions and amendments |
| 6 | **Probe Artifacts** | Machine-readable evidence and results |
| 7 | **Research Monograph** | Explanatory architecture and overall research vision |

**Precedence.** The Phase 1 charter overrides less precise monograph language. This master plan preserves that precedence: it sequences work; it does not redefine intelligence, capability, learning, transfer, or authority.

---

## 1. Programme thesis

> Astra compiles verified procedural capabilities from experience.

The model is temporary reasoning support. Authority, evidence, execution, verification, and accumulated procedural intelligence belong to Astra. Improvement is measured over memory-only baselines (B2), under fixed resources and a fixed safety kernel, separately for within-repository adaptation and cross-repository transfer, plus survival under model replacement or removal (B5).

**Experimental unit.** A *project count* in this programme means a **canonical software-engineering episode**: one bounded user objective executed through one canonical Astra project lifecycle. Results must report both episode count and independent-repository count. Multiple episodes from one repository are not statistically independent repository-transfer cases.

---

## 2. Where Astra is now

| Layer | Status | Notes |
|---|---|---|
| Deterministic baseline (Phase 0) | **Complete** | Canonical lifecycle, approvals, Docker isolation, LocalAIService, semantic edits, retrieval, 40/40 phase0.v1 benchmark |
| Research identity (Phase 1) | **Frozen** | Charter definitions; reopen only by amendment |
| Knowledge / grammars (Phase 2) | **Complete** (housekeeping remains) | FastAPI + pytest grammars; distinct graphs; declared gaps; no fixture-identity features |
| Evidence architecture (Phase 3) | **In progress** | Observation / interpretation / assessment split; recomputation probe |
| Trajectory → compiler → transfer → governance | **Not started** | Phases 4–11 |
| Decision learning | **Emerging baseline only** | Advisory ranker / decision spine — not a capability compiler |
| Longitudinal / model-independence / capstone | **Not started** | Phases 13–15 |
| Track E (engineering health) | **Ongoing parallel** | Complexity debt; must not masquerade as research gain |

The monograph is explicit: the current decision/outcome spine is **not** a capability compiler.

---

## 3. What is frozen vs experimental

### Frozen (amendment required)

- Safety / authority / approvals / workspace / verification kernel
- Capability object \(C = (A, P, I, V)\)
- Immutable observations; versioned interpretations; recomputable derived assessments
- Compiler recommendation-only authority (no self-promotion)
- Preference quarantine and lowest-authority preference rules
- Capability-relative transfer; held-out means uninvolved
- Repository-clustered evaluation discipline
- “Capability count is not intelligence”
- Document hierarchy and charter precedence

### Experimental (must remain fail-closed / shadow until gates pass)

- Pattern mining and anti-unification
- Learned applicability and strategy ranking
- Capability promotion and production execution
- Longitudinal growth claims (B0–B5)
- Model-independence claims
- Any production import from `research/` packages

---

## 4. Dependency chain (critical path)

```text
Phase 1 definitions
    ↓
Phase 2 grammar
    ↓
Phase 3 immutable evidence
    ↓
Phase 4 normalized trajectories
    ↓
Phase 5 compiler contracts
    ↓
Phase 6 discovery
    ↓
Phase 7 applicability
    ↓
Phase 8 replay and mutation
    ↓
Phase 9 held-out transfer
    ↓
Phase 10 governance
    ↓
Phase 11 authorized execution
    ↓
Phase 12 decision learning
    ↓
Phase 13 longitudinal experiment
    ↓
Phase 14 model independence
    ↓
Phase 15 capstone delivery
```

### Must not be built early

| Forbidden early step | Blocked until |
|---|---|
| Miner / discovery algorithms | Evidence recomputation (Phase 3 gate) |
| Learned applicability | Normalized positive **and** negative examples (Phase 4) |
| Promotion / lifecycle mutation | Replay + held-out evaluation (Phases 8–9) + governance (Phase 10) |
| Production capability execution | Governance + authorized library path (Phases 10–11) |
| Intelligence-growth claim | Chronological B0–B5 baselines (Phase 13) |
| Model-independence claim | Model swap or removal experiment (Phase 14) |
| Production imports from research packages | Explicit promotion gate + Track E review |

---

## 5. Phase programme

### Phase 0 — Existing deterministic baseline

**Purpose.** Establish that Astra already has a functioning, bounded engineering system before introducing learning.

**Existing evidence.** Canonical project lifecycle; approvals and idempotency; bounded Docker validation; `LocalAIService`; deterministic semantic edits; retrieval and repository analysis; **40/40** deterministic benchmark (`phase0.v1`); worker and frontend flow.

**Status.** Complete as baseline, not frozen forever.

**Remaining maintenance.** Preserve regression tests; avoid production integration from research packages; reduce complexity debt on Track E; do not mix architectural cleanup with experimental results.

**Gate.** A reproducible static baseline with exact artifacts, tests, hardware configuration, and no learned components required for operation.

**Claims allowed.** “Astra operates as a bounded deterministic engineering assistant under a fixed safety kernel.”  
**Claims forbidden.** Capability growth, transfer, model independence, learned intelligence.

---

### Phase 1 — Research identity and epistemic charter

**Purpose.** Define intelligence, evidence, experience, capability, learning, transfer, authority, and success.

**Frozen results.** \(C=(A,P,I,V)\); canonical engineering episode; immutable observations / recomputable interpretations; fixed authority kernel; compiler recommendation-only; preference quarantine; capability-relative transfer; repository-clustered evaluation; held-out = uninvolved; capability count ≠ intelligence.

**Status.** Complete and frozen.

**Reopening rule.** Explicit charter amendment only.

**Gate.** Frozen charter with amendment log; monograph language subordinate to charter.

**Claims allowed.** Definitional and normative claims about programme identity.  
**Claims forbidden.** Empirical performance claims beyond definitional probes.

---

### Phase 2 — Knowledge representation and domain grammars

**Purpose.** Define the legal procedural search space before discovery.

**Completed work.** Immutable grammar contracts; canonical serialization and hashing; operations vs bindings; FastAPI grammar; pytest grammar; evidence features; invariants; verification contracts; graph-level integration verification; declared capability gaps; grammar linting; manual traces; cross-grammar structural comparison.

**Current result (operator-reported probes).**

| Grammar | Ops | Evidence features | Invariants | Verifiers / contracts | Supported fixtures | Distinct graphs | Declared gaps | Fixture-specific features |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FastAPI | 7 | 8 | 6 | 4 | 3 | 3 | 1 | 0 |
| Pytest | 6 (+1 typed binding) | 6 | 4 | 3 | 3 | 3 | 1 | 0 |

**Supported claim.** Two structurally distinct bounded grammars can represent multiple evidence-conditioned procedures without fixture identity or unrestricted generation.

**Not yet supported.** Automatic discovery; learned applicability; transfer; promotion; capability growth.

**Status.** Knowledge-representation foundation complete.

**Remaining housekeeping.** Apply patch-level grammar versions; freeze operation-versus-binding rule in the decision index; record granularity pressure points; ensure all Phase 2 artifacts are hash-bound and reproducible in `artifacts/research/`.

**Gate.** Two grammars, each with multiple valid graphs, mechanistic witnesses, independent integration verification, declared gaps, and no fixture-identity features.

---

### Phase 3 — Evidence architecture

**Purpose.** Preserve enough immutable information to recompute future claims without depending on mutable repository state.

**Core architecture.**

```text
Observation layer      → immutable source and event references
Interpretation layer   → versioned extractor outputs
Assessment layer       → recomputable derived claims
```

This operationalizes: outcome history belongs to immutable experiences; capability statistics are recomputable projections.

**Required deliverables.** `CanonicalEngineeringEpisode`; observation references; repository snapshot identity; feature interpretations; explicit missingness; extractor and vocabulary versions; derived assessments; supersession; append-only research store; recomputation engine; leakage validation; preference quarantine; evidence recomputation probe.

**Status.** In implementation.

**Scientific question.** Can Astra recompute a derived claim under a new evidence vocabulary while preserving original observations and the historical assessment unchanged?

**Gate.** Probe demonstrates: unchanged observation hashes; new interpretations under vocabulary v2; superseded but unmodified v1 assessment; explicit changed result; no held-out leakage; no preference contamination; no episode fragmentation.

**Claims allowed after gate.** “Derived assessments are recomputable under vocabulary change without mutating observations.”  
**Claims forbidden until gate.** Any compiler, applicability, or transfer result that depends on mutable working-tree state alone.

---

### Phase 4 — Procedural dataset and trajectory normalization

**Purpose.** Convert canonical engineering episodes into clean, versioned procedural traces for compiler research.

**Required work.** Normalize operations, bindings, inputs, outputs, failures, verification; represent positive, failed, rejected, abstained, and rolled-back episodes; preserve alternatives shown; separate model vs deterministic contribution; define trajectory equivalence; inclusion/exclusion rules; chronological and repository-lineage splits; negative and counterexample datasets.

**Key risk.** Success-only datasets create self-confirming, overgeneralized procedures.

**Gate.** A reproducible dataset where every trajectory links to immutable observations, exact vocabulary versions, and a canonical outcome taxonomy.

**Status.** Not started (blocked on Phase 3 gate).

---

### Phase 5 — Manual capability compiler baseline

**Purpose.** Build compiler interfaces before discovery algorithms.

**Required work.** Candidate capability contract; typed procedural IR; applicability predicate representation; invariant bundle; verification bundle; distinguishing witness; compilation batch identity; candidate dossier; manual candidate construction; static type checking; simulation / replay / held-out evaluation **requests**.

**Restriction.** Candidates remain research artifacts only. The compiler may propose and request evaluation; it cannot promote or modify lifecycle state.

**Gate.** A human-authored candidate passes the entire compiler pipeline without production activation.

**Status.** Not started.

---

### Phase 6 — Pattern mining and bounded anti-unification

**Purpose.** Determine whether repeated procedural structure can be discovered from normalized trajectories.

**Required work.** Operation-graph clustering; typed graph matching; bounded anti-unification; parameter extraction; dependency preservation; negative-example use; unresolved identity state; provisional split candidates; vocabulary inadequacy detection.

**Baselines.** Exact graph repetition; manually fixed template; nearest-neighbor reuse; clustering without negatives; bounded anti-unification.

**Scientific question.** Can the compiler identify reusable causal procedure structure beyond exact replay without collapsing distinct procedures?

**Gate.** Candidate procedures outperform exact-template recovery on held-out trace reconstruction while preserving type, invariant, and verifier compatibility.

**Status.** Not started. **Forbidden** before Phase 3–5 gates.

---

### Phase 7 — Applicability learning

**Purpose.** Learn when a candidate procedure should activate.

**Required work.** Deterministic applicability baseline; rule learners or small transparent models; positive / negative / abstention / invalid-evaluation labels; missing-feature semantics; calibration; false-applicability analysis; correct-abstention measurement; retroactive episode membership recomputation; vocabulary-version sensitivity.

**Baselines.** Task-family-only; framework-only; repository-nearest-neighbor; manually authored predicate; learned transparent classifier.

**Gate.** Applicability improves activation precision or coverage over static rules without increasing unsafe activation or hiding alternative procedures inside the predicate.

**Status.** Not started.

---

### Phase 8 — Simulation, mutation, and historical replay

**Purpose.** Attack candidate capabilities before held-out evaluation.

**Required work.** Boundary mutation; missing-evidence mutation; stale binding tests; invariant attacks; verifier mismatch; dependency drift; revoked child behavior; historical snapshot replay; changed predicate recomputation; graph-level integration failure tests.

**Gate.** Every candidate has simulation, mutation, and historical replay reports; known failure boundary; explicit unresolved cases.

**Status.** Not started.

---

### Phase 9 — Held-out transfer evaluation

**Purpose.** Test whether unchanged capabilities work outside their source contexts.

**Per-claim reporting.** Capability version; source/target profiles; evidence-vocabulary version; changed-dimension signature; transfer stratum; applicability / procedure / invariant / verification results; repository lineage.

**Outcomes.** Transfer success; correct abstention; false applicability; procedural failure; invariant failure; verification failure; invalid evaluation.

**Gate.** Repository-disjoint evaluation with clustered uncertainty and no target involvement in grammar, verifier, predicate, or threshold design.

**Status.** Not started.

---

### Phase 10 — Capability governance and lifecycle

**Purpose.** Authority-controlled lifecycle decisions after the research pipeline works.

**Lifecycle states.** observed → candidate → probationary → replay-verified → experimental → production → degraded → deprecated → revoked.

**Required work.** Governance contracts; exact version and dossier binding; actor authority; deterministic transition matrix; promotion thresholds; degradation/revocation; dependency propagation; idempotency; stale recommendation rejection; immutable governance decisions.

**Gate.** Compiler cannot mutate lifecycle state directly; every transition is reproducible, authorized, and auditable.

**Status.** Not started.

---

### Phase 11 — Capability library and execution

**Purpose.** Execute only governance-authorized capability versions.

**Required work.** Immutable package storage; dependency graph; execution adapter; trusted operation handler binding; bounded model slots; capability selection; runtime evidence checks; verifier invocation; dependency revocation; explicit fallback; shadow execution mode.

**First deployment mode.** research-only → shadow → explicit opt-in → experimental → production candidate.

**Gate.** A promoted capability executes through the canonical project-control path without bypassing approvals, scope, isolation, artifact identity, or verification.

**Status.** Not started.

---

### Phase 12 — Strategy ranking and decision learning

**Purpose.** Learn which authorized strategy or capability to choose.

**Required work.** Rules baseline; memory-only baseline; transparent ranking models; calibration; regret; failure prediction; clarification recommendation; uncertainty threshold; shadow mode; ranker cannot introduce new strategies.

**Gate.** Ranker improves selection efficiency or success while unsafe-selection rate remains zero and deterministic fallback remains available.

**Status.** Emerging baseline only (existing advisory decision/ranker spine). Not a promotion path.

---

### Phase 13 — Chronological growth experiment

**Purpose.** Test the central intelligence hypothesis.

**Baseline ladder.**

| ID | System | Purpose |
|---|---|---|
| B0 | Static deterministic | Non-neural competence |
| B1 | Static + SLM | Model contribution |
| B2 | Memory only | Separates recall from learning (must be strong) |
| B3 | Strategy ranker | Policy learning among fixed capabilities |
| B3.5 | Prompt-guidance control | Same procedures as text vs deterministic execution |
| B4 | Capability compiler | Central contribution |
| B5 | Model swap / removal | Model independence |

Comparable under fixed models, prompts, hardware, evidence, approval policy, and evaluation cases within an epoch.

**Checkpoints.** 0, 100, 250, 500, 750, 1000 canonical engineering episodes — also report independent repositories, lineage clusters, task-family distribution, coverage, reliability, correct abstention, false applicability, model calls, generated characters, GPU time, human burden, regression cost, library maintenance cost.

**Gate.** Compiled capabilities outperform static, memory-only, and ranker-only baselines on repository-disjoint held-out tasks without weakening safety or merely narrowing coverage.

**Status.** Not started. Pre-registration required before Phase 6/compiler claims are treated as confirmatory.

---

### Phase 14 — Model-independence experiment

**Purpose.** Determine whether learned intelligence belongs to Astra rather than the model.

**Conditions.** Original model; different small model; weaker model; no model where capabilities are fully deterministic.

**Gate.** Previously compiled capabilities retain meaningful utility after model replacement or removal.

**Status.** Not started.

---

### Phase 15 — Consolidation and capstone delivery

**Purpose.** Turn the research programme into a defensible academic submission.

**Required outputs.** Final system; frozen repository tag; architecture report; methodology; experimental results; negative results; threats to validity; demonstration scenario; presentation; reproducibility instructions; artifact index; examiner Q&A.

**Gate.** Another person can reproduce the principal results from the repository, frozen configuration, and documented commands.

**Status.** Not started.

---

## 6. Parallel Track E — Engineering health

Research phases must not absorb every engineering problem.

| Work | Rule |
|---|---|
| Reduce `main.py`, project-control concentration, `App.tsx` | Separate PRs; no claim of research gain |
| Consolidate legacy vs canonical routes | Preserve one worker, one model boundary, one retrieval owner |
| Full-suite regression health | Required before any experimental promotion |
| WSL / Docker / developer tooling | Runtime observations recorded per experiment |

Ordinary refactoring must not masquerade as research improvement.

---

## 7. Master status dashboard

| Phase | Status | Evidence | Next gate |
|---|---|---|---|
| 0 Deterministic baseline | Complete | 40/40 benchmark, canonical control plane | Preserve regression |
| 1 Research charter | Frozen | Phase 1 charter | Amendment only |
| 2 Grammar foundation | Complete | FastAPI and pytest probes | Final versioning cleanup |
| 3 Evidence architecture | In progress | Implementation under way | Recompute v1 → v2 without mutation |
| 4 Trajectory normalization | Not started | — | Canonical dataset |
| 5 Compiler contracts | Not started | — | Manual end-to-end candidate |
| 6 Discovery | Not started | — | Held-out trace abstraction |
| 7 Applicability | Not started | — | Calibrated activation |
| 8 Replay/mutation | Not started | — | Candidate robustness |
| 9 Transfer | Not started | — | Repository-disjoint result |
| 10 Governance | Not started | — | Authorized lifecycle |
| 11 Execution | Not started | — | Shadow capability run |
| 12 Decision learning | Emerging baseline only | Advisory ranker / decision spine | Shadow improvement vs rules |
| 13 Longitudinal study | Not started | — | B0–B5 comparison |
| 14 Model independence | Not started | — | Model swap/removal |
| 15 Capstone delivery | Not started | — | Reproducible submission |

Authoritative machine copy: `docs/astra-next-master-status.json`.

---

## 8. Implementation, test, and experiment map

| Phase | Implement | Test | Experiment |
|---|---|---|---|
| 0 | Maintain deterministic core | Regression + phase0.v1 | None (baseline only) |
| 1 | Charter + amendment log | Editorial consistency probes | None |
| 2 | Grammar packages + hashes | Lint, fixture traces, cross-grammar compare | Manual discovery probe only |
| 3 | Episode store, extractors, assessments | Recomputation probe, leakage, preference quarantine | Vocabulary v1→v2 recompute |
| 4 | Trajectory normalizer + splits | Schema round-trip, taxonomy coverage | Dataset quality audit |
| 5 | Compiler IR + dossier + request APIs | Manual candidate pipeline | No discovery yet |
| 6 | Miner + anti-unification | Type/invariant/verifier checks | vs exact template / NN baselines |
| 7 | Applicability models | Calibration, abstention, false-activation | vs static predicates |
| 8 | Mutation + replay harness | Boundary and drift suites | Robustness of each candidate |
| 9 | Transfer evaluator | Lineage / held-out audits | Strata 0–5 claims |
| 10 | Governance FSM | Authorization + idempotency | Promotion policy dry-runs |
| 11 | Library + execution adapter | Shadow then opt-in | End-to-end authorized run |
| 12 | Ranker shadow → promote | Unsafe-selection = 0 | vs rules + memory |
| 13 | Chronological harness | Leakage + clustering stats | B0–B5 at checkpoints |
| 14 | Model swap harness | Capability survival metrics | Model replace/remove |
| 15 | Packaging + docs | External reproduction checklist | Examiner dry-run |

---

## 9. Immediate next actions

1. **Finish Phase 3 gate** — evidence recomputation probe (observation hashes stable; v1 assessment superseded, not rewritten).
2. **Phase 2 housekeeping** — hash-bind grammar artifacts into `artifacts/research/`; freeze operation-vs-binding in the decision index.
3. **Do not start Phase 6+** until Phases 3–5 gates pass.
4. **Track E** — continue complexity reduction on a separate track from research PRs.
5. **Pre-register Phase 13 protocol** before treating any compiler result as confirmatory.

---

## 10. Amendment policy

- Changes to **definitions / authority / evidence rules** → Phase 1 charter amendment + decision-index entry.
- Changes to **sequence, gates, or status** → update this master plan + `astra-next-master-status.json` in the same commit.
- Changes to **phase-local rules** → that phase’s charter/protocol; indexed here.
- Monograph updates are explanatory; they never silently override the charter or this plan’s gates.
