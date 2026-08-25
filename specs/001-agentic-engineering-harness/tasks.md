# Tasks: Agentic Engineering Harness (Code Review & Assurance)

**Input**: Design documents from `/specs/001-agentic-engineering-harness/` (spec.md ✅, plan.md ✅)

**Tests**: Included — spec defines measurable SCs requiring contract/integration/perf verification.

**Organization**: Grouped by phase and user story. Milestone mapping from plan.md shown per phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable (different files, no dependencies)
- **[Story]**: US1=PR/commit review · US2=MCP pre-commit · US3=Risk depth · US4=Finding lifecycle · US5=Lens set · US6=State queries

---

## Phase 1: Setup

**Purpose**: Repo scaffolding and toolchain (plan.md §Source Structure)

- [ ] T001 Create monorepo skeleton exactly per plan.md tree (`src/harness`, `src/providers`, `src/controlplane`, `src/mcpserver`, `src/worker`, `src/lenses`, `src/graph`, `src/eval`, `benchmark/`, `infra/`, `tests/{contract,integration,unit}`)
- [ ] T002 Initialize `uv` workspace `pyproject.toml`: Python 3.12, pinned deps (`agent-framework`, `fastapi`, `mcp`, `azure-servicebus`, `azure-cosmos`, `azure-storage-blob`, `azure-identity`, `azure-monitor-opentelemetry`)
- [ ] T003 [P] Configure ruff lint/format + CI lint gate
- [ ] T004 [P] Configure pytest + pytest-asyncio + coverage thresholds
- [ ] T005 Bicep foundation: Container Apps environment, ACR, user-assigned managed identities, resource group conventions (`infra/main.bicep`)

## Phase 2: Foundational (BLOCKING — all stories depend on this)

**Purpose**: Domain library, storage, messaging, auth, telemetry

- [ ] T006 Pydantic v2 domain models: Change, ReviewRun, Finding, RiskAssessment, Specification in `src/harness/models/`
- [ ] T007 Finding lifecycle state machine in `src/harness/lifecycle.py`; transitions re-validate via `model_validate(model_dump())`; **regression test required** (known-pitfall: `model_copy(update=)` skips validators)
- [ ] T008 Secret redaction in `src/harness/redaction.py` + pattern unit tests (FR-020)
- [ ] T009 CanonicalEvent schema v1 in `src/harness/events/`; export JSON Schema to `specs/001-agentic-engineering-harness/contracts/events.v1.schema.json`; Service Bus topic emitter helper (FR-021/022)
- [ ] T010 Dedup key component `src/harness/dedup.py` (path + normalized content hash) behind swappable interface + unit tests (FR-015)
- [ ] T011 Risk signal aggregation skeleton `src/harness/risk.py` (FR-011 types only; logic lands in US3)
- [ ] T012 Provider adapter protocol `src/providers/base.py`: parse_webhook→Change, fetch_diff, post_annotations
- [ ] T013 Service Bus queues/topics provisioned (`infra/`); enqueue/consume wrapper with at-least-once semantics + poison-queue handling; integration test proves no-loss under worker crash (FR-017)
- [ ] T014 Cosmos DB containers + repository layer with partition-key design per plan.md data model; health probe sets `RequestTimeout≥10s` and 8 s cancellation (known-pitfall: Cosmos cold-start)
- [ ] T015 Blob evidence store helper `src/harness/evidence.py` (diff snapshots, large artifacts)
- [ ] T016 Shared Entra auth middleware + per-repo scoping dependency usable by both hosts (FR-019)
- [ ] T017 OpenTelemetry→App Insights wiring; structured logging conventions incl. no PII/secrets rule
- [ ] T018 Cost budget accounting hooks `src/worker/budget.py` with degraded-mode result state type (FR-035 mechanics; ceilings configured in US3)

**Checkpoint**: Foundation ready — benchmark and US1 may start.

## Phase 3: Benchmark Gate (M0 — gates ALL lens/model integration)

**Purpose**: Measure detection reality before building on top of it (plan.md M0 rationale)

- [ ] T019 [P] Scoring harness `src/eval/score.py`: planted-defect detection matrix + false-positive rate on clean repos
- [ ] T020 [P] Correctness-class seeded cases (~5) in `benchmark/cases/correctness/`
- [ ] T021 [P] Security-class seeded cases (~5) in `benchmark/cases/security/`
- [ ] T022 [P] Structural-class cases (~5) derived from thermo-nuclear fake-wiring pattern classes in `benchmark/cases/structural/`
- [ ] T023 [P] Silent-failure cases (~5) from production-validation taxonomy in `benchmark/cases/production_validation/`
- [ ] T024 [P] Adversarial prompt-injection corpus (~10) seeded from `.apm/skills/threat-modelling/references/ai-agentic-threat-catalogue.md` in `benchmark/cases/injection/`
- [ ] T025 Baseline runner: current frontier models via Foundry over corpus; results to `docs/benchmark-baseline.md`
- [ ] T026 GO/NO-GO review vs SC-004 targets; if NO-GO: iterate prompts/context strategy and re-run — **no lens production code until pass**

**Checkpoint**: Detection baseline recorded. Gates Phases 4(lens parts)/8; adapter/infra tasks may proceed in parallel.

## Phase 4: User Story 1 — Automated PR/Commit Review (P1) 🎯 MVP

**Goal**: Webhook→review→posted findings, zero local install, zero Actions minutes
**Independent Test**: Open PR with known defect → findings posted ≤5 min p95; zero Actions runs triggered

### Tests (write first, watch them fail)

- [ ] T027 [P] [US1] Contract tests: GitHub webhook fixture parsing + signature validation in `tests/contract/test_github_adapter.py`
- [ ] T028 [P] [US1] Contract tests: Azure DevOps webhook fixtures in `tests/contract/test_azuredevops_adapter.py`
- [ ] T029 [P] [US1] Integration test skeleton: enqueue→consume→persist→annotate happy path with Git-host test double in `tests/integration/test_review_pipeline.py`

### Implementation

- [ ] T030 [US1] GitHub adapter `src/providers/github/` (T012 protocol)
- [ ] T031 [US1] Azure DevOps adapter `src/providers/azuredevops/`
- [ ] T032 [US1] `src/controlplane/webhooks.py`: HMAC/signature verification, replay dedupe, classification (code/docs/generated), enqueue; declare `response_model=None` on mixed-shape routes; emit `Retry-After` on 429 (known-pitfalls)
- [ ] T033 [US1] Lens plugin registry + uniform evidence contract `src/lenses/base.py` (finding must carry diff location, rule/spec ID, metrics — or be marked unsupported) (FR-010)
- [ ] T034 [US1] Worker executor `src/worker/runner.py`: message→change resolution→lens invocation→run persistence (blocked until T026 passes for model calls)
- [ ] T035 [US1] Correctness lens via Agent Framework workflow + Foundry call (budget-aware)
- [ ] T036 [US1] Security lens (boundary-aware prompting; authorization-change detection)
- [ ] T037 [US1] Annotation projection back to PR/commit via adapters; structured comment format
- [ ] T038 [US1] Superseded-run marking when head SHA advances mid-review (edge case)
- [ ] T039 [US1] Failure attribution V1 subset `src/worker/attribution.py` + stop conditions `src/worker/stopconditions.py`: N-retry cap, oscillation guard, budget-exhausted stop (FR-024/025 V1 scope)
- [ ] T040 [US1] Concurrency proof: 100 parallel enqueues → all complete, none dropped, bounded delay (Story-1 acceptance #4)

**Checkpoint**: Story 1 demoable end-to-end. STOP and validate independently.

## Phase 5: User Story 2 — MCP Pre-Commit Review (P2)

**Goal**: Any agent calls `review.validate_change` with a working diff; nothing installed locally
**Independent Test**: Stock MCP client submits defective diff → structured findings; unauthenticated request rejected

- [ ] T041 [P] [US2] Contract tests for all MCP tool schemas in `tests/contract/test_mcp_tools.py`
- [ ] T042 [US2] `src/mcpserver/auth.py`: Entra token validation, repo scoping enforcement, clear auth errors (FR-019)
- [ ] T043 [US2] `src/mcpserver/tools_review.py`: `review.validate_change` — sync path ≤500 changed lines, else async job handle (FR-005)
- [ ] T044 [US2] Priority lane routing interactive requests ahead of event-triggered queue depth (protects SC-003 p95)
- [ ] T045 [US2] Org-managed client configuration guide `docs/mcp-onboarding.md` + config snippet generator (SC-006: zero-setup-beyond-auth)
- [ ] T046 [US2] Cross-agent equivalence test: identical input via two client profiles → equivalent structured output
- [ ] T047 [US2] Latency instrumentation + perf test asserting SC-003 (≤60 s p95 @500 lines)

**Checkpoint**: Stories 1+2 independent and functional.

## Phase 6: User Story 3 — Risk-Proportionate Depth (P3)

- [ ] T048 [P] [US3] Unit tests for signal aggregation in `tests/unit/test_risk.py`
- [ ] T049 [US3] Implement signals in `src/harness/risk.py`: security-boundary impact, public-surface delta, deployment target, test-coverage delta, incident-history lookup hook (FR-011)
- [ ] T050 [US3] Risk→lens selection + depth policy (low/docs-only fast path; high→security deep pass; critical→acknowledgement) (FR-012, edge cases)
- [ ] T051 [US3] Blocking-category merge-gate status reporting via adapters (FR-013)
- [ ] T052 [US3] Critical-risk human-acknowledgement flow before merge-gate passes
- [ ] T053 [US3] Per-review cost ceiling defaults wired from config into `budget.py`; degraded runs marked partial-explicit (FR-035 completion)

**Checkpoint**: Docs-only change → low risk/minimal lenses; identity-Terraform change → high risk/security lens (Story-3 acceptances).

## Phase 7: User Story 4 — Finding Lifecycle (P4)

- [ ] T054 [P] [US4] Lifecycle transition tests incl. illegal-transition rejection in `tests/unit/test_lifecycle.py`
- [ ] T055 [US4] Stale detection: force-push/diff-removal resolves-or-stales referenced findings (acceptance #1)
- [ ] T056 [US4] Dedup application across PR commits via `dedup.py`; orphan-duplicate prevention
- [ ] T057 [US4] Reopen linkage: reintroduced defect reopens original finding with commit link, not duplicate (acceptance #2)
- [ ] T058 [US4] Waiver flow: immutable sub-document (approver, timestamp, rationale) + admin API in `src/controlplane/admin.py` (FR-016)
- [ ] T059 [US4] Immutability enforcement test: waiver mutation attempts fail at repository layer

## Phase 8: User Story 5 — Full Lens Set (P5) *(requires Phase 3 PASS)*

- [ ] T060 [P] [US5] Architecture lens
- [ ] T061 [P] [US5] Test-quality lens
- [ ] T062 [US5] Structural lens: port thermo-nuclear methodology — fake-wiring scan classes, 1k-line growth rule, Blocker severity, `[VERIFY]` discipline; operates on diff scope, emits Findings (source: `.apm/skills/thermo-nuclear-code-quality-review/SKILL.md`)
- [ ] T063 [US5] Production-validation lens: port defined≠wired≠invoked≠working≠observable taxonomy + portable `static_gate.py` heuristics (source: `.apm/skills/production-validation/`)
- [ ] T064 [US5] Lens failure isolation: injected lens exception → remaining lenses report, failure attributed per-lens, run neither silently passes nor fails (FR-009)
- [ ] T065 [US5] CI benchmark regression gate: build fails on detection-rate drop below SC-004 thresholds (SC-009)
- [ ] T066 [US5] CI injection-corpus gate: injection attempts never alter outcomes (FR-034 continuous verification)
- [ ] T067 [US5] Silent-failure acceptance test: seeded registered-but-never-called service case flagged specifically (Story-5 independent test)

## Phase 9: User Story 6 — Queryable Engineering State (P6)

- [ ] T068 [P] [US6] Repository discovery scanner `src/graph/scanner.py` (code entities) (FR-033)
- [ ] T069 [P] [US6] Declared-entity loader `src/graph/declared.py` (specs, teams, environments, deployments)
- [ ] T070 [US6] Graph merge into `graphEntities` + spec-applicability resolution consumed by worker spec resolver
- [ ] T071 [US6] `src/mcpserver/tools_state.py`: `get_risk_explanation`, `get_active_findings`, `get_related_changes` (exclude stale/waived; link sources)
- [ ] T072 [US6] Grounding test: known risk-driver PR query enumerates exactly those drivers (Story-6 acceptance #1)

## Phase 10: Polish & Cross-Cutting

- [ ] T073 [P] Observability completion: every KQL alert paired with verified emitting call site (known-pitfall: orphaned alerts); operational dashboard
- [ ] T074 [P] Retention TTL configuration per deployment, default 12 months, applied to `episodes`/evidence (SC-008)
- [ ] T075 Engagement isolation verification: cross-partition access attempts fail; per-engagement spec/memory stores proven isolated (Assumptions)
- [ ] T076 End-to-end redaction verification: secrets planted in test diffs never appear in persisted state or projections (FR-020)
- [ ] T077 Threat-model walkthrough against ai-agentic threat catalogue; file deltas into mitigation backlog (`.apm/skills/threat-modelling/templates/mitigation-backlog.md`)
- [ ] T078 Author `quickstart.md` (deploy → configure repo → first review → first MCP call); validate on clean clone
- [ ] T079 SC scorecard: measure and publish results for SC-001..SC-009 in `docs/validation-results.md`
- [ ] T080 Phase-2 non-goals register: AGT governance manifests, workspaces, tool tiers, memory tiers, hygiene agent — documented as explicit deferred scope (FR-023, FR-026–030)

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (1)** → **Foundational (2)**: strict sequence; T013–T018 block everything downstream
- **Benchmark (3)**: starts after T011/T018 minimal support; **gates T034–T036 and all of Phase 8**; parallel-safe with adapter/infra tasks
- **US1 (4)** ← Foundational; lens tasks additionally gated by Phase 3
- **US2 (5)** ← needs worker path (T034) but MCP contract tests can start earlier
- **US3 (6)** ← needs ≥2 lenses live for meaningful risk-depth behavior
- **US4 (7)** ← needs findings flowing (US1)
- **US5 (8)** ← strictly after Phase 3 GO
- **US6 (9)** ← needs persisted findings/risk (US1/US3)
- **Polish (10)** ← last; T079 requires all prior phases

### Critical Path
T001 → T002 → T006..T018 → T019..T026 → T030..T040 → T041..T047 → T049..T053 → T055..T059 → T060..T067 → T068..T072 → T073..T080

### Parallel Opportunities
- All [P] tasks within a phase
- Phase 3 corpus seeding (T020–T024) fully parallel
- Adapters (T030/T031) parallel; lenses T060/T061 parallel; graph scanners T068/T069 parallel
- US2 contract tests (T041) before worker exists

## Implementation Strategy

- **MVP**: Phases 1–4 → demoable PR review with 2 lenses. Validate SC-001/SC-002 mechanics before continuing.
- **Gate discipline**: Phase 3 NO-GO loops on prompting only — do not build pipeline features to compensate for weak detection.
- **Per-task commits**: one logical group each; benchmark corpus changes always accompanied by scorecard delta.
- **Known-pitfall tests are mandatory**, not advisory (T007, T014, T032, T073).
