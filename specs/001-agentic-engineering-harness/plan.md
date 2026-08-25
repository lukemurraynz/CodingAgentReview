# Implementation Plan: Agentic Engineering Harness (Code Review & Assurance)

**Branch**: `001-agentic-engineering-harness` | **Date**: 2026-08-25 | **Spec**: [specs/001-agentic-engineering-harness/spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-agentic-engineering-harness/spec.md`

## Summary

Centrally hosted code-review and engineering-assurance platform on Microsoft Foundry + Agent Framework + Azure Container Apps. Receives PR/commit events via webhooks (GitHub + Azure DevOps adapter contract), executes composable review lenses as Agent Framework workflows, computes per-change risk, persists a durable finding lifecycle, and exposes everything through an MCP server usable pre-commit by any coding agent — with zero repository-local installation and zero consumption of GitHub Actions minutes or customer-held model credentials. V1 is review-only; autonomy machinery (task state machine beyond execution, workspaces, tool tiers, completion contracts) is phase-gated.

## Technical Context

**Language/Version**: Python 3.12 (Agent Framework Python SDK is first-class for this stack; team skills library is Python-centric — see `.apm/skills/python-patterns`)

**Primary Dependencies**:
- `agent-framework` (Microsoft Agent Framework) — workflow orchestration, agent loop primitives, checkpointing
- `fastapi` + `uvicorn` — webhook receiver & internal API
- `mcp` (Model Context Protocol Python SDK) — MCP server surface
- Azure SDK: `azure-servicebus`, `azure-cosmos`, `azure-storage-blob`, `azure-identity`, `azure-monitor-opentelemetry`
- Microsoft Foundry (hosted inference; model-diverse per FR-004 — lens configs pin provider+model per deployment)

**Storage**: Cosmos DB (NoSQL — findings, runs, risk, specs, graph); Blob Storage (diff evidence, large artifacts); Service Bus (durable trigger→worker dispatch, FR-017). No relational store.

**Testing**: `pytest` + `pytest-asyncio`; contract tests for MCP tools & webhook adapters; seeded-defect benchmark suite (`benchmark/`) gates lens changes per SC-009. Adversarial prompt-injection corpus lives inside the same benchmark (FR-034 verification).

**Target Platform**: Azure Container Apps — Express profile for control-plane API + MCP server (low-traffic request/response services); **Container Apps Jobs** for review workers (bounded, isolated, scale-to-zero). NOT Express for workers: filesystem/process isolation requirements exceed Express guarantees (per conversation constraint).

**Project Type**: Multi-service Python monorepo (single repo, deployable components)

**Performance Goals**: PR-open→review-posted p95 ≤ 5 min (SC-001); MCP pre-commit round-trip p95 ≤ 60 s for diffs ≤ 500 changed lines (SC-003); ≥95% trigger-to-run-start ≤ 30 s

**Constraints**:
- Zero Actions minutes / zero customer-held model credentials (FR-004)
- Per-review cost ceiling with explicit degraded-mode reporting (FR-035)
- Entra ID authn end-to-end incl. MCP; per-repo scoping (FR-019); secret redaction before persistence (FR-020)
- Lens failure isolation — one failing lens cannot corrupt the run (FR-009)
- Engagement isolation of specs/memory when deployed as accelerator (Assumptions)
- Reviewed content is untrusted input everywhere (FR-034)

**Scale/Scope**: Single organization, both Git adapters, 6 lenses, ~10s of repos initially; designed for 100 concurrent reviews without drops (SC Story-1 acceptance #4).

## Constitution Check

*No project constitution file exists yet (`/speckit.constitution` not run). Gate applied against default SDD principles + the workspace known-pitfalls register (`E:\GitHub\Coding\.apm\known-pitfalls.md`):*

| Principle | Status | Notes |
|---|---|---|
| Library-first, thin entry points | ✅ PASS | Domain logic in `harness/` package; `controlplane/`, `mcpserver/`, `worker/` are thin hosts |
| No hand-rolled agent governance | ✅ PASS | Phase-2 tool policy/approvals MUST use Agent Governance Toolkit manifests, not custom evaluators (known-pitfall: fabricated parallel policy engines). V1 has no autonomous actions, so AGT adoption lands with Phase 2 |
| Evidence-grounded findings only | ✅ PASS | FR-010 mirrors thermo-nuclear operating rules (cite file:line or mark unsupported) |
| Schema fields assert only implemented guarantees | ✅ PASS | Degraded/partial results are explicit states, never defaulted to complete (known-pitfall: semantically-empty conformance fields) |
| Every alert has a verified emitter | ⚠️ ACTION | Observability section of tasks must pair each KQL alert with its emitting log/metric call site |
| Reviewed content treated as data, never instructions | ✅ PASS | FR-034; both lens methodologies already encode this rule |

## Project Structure

### Documentation (this feature)

```text
specs/001-agentic-engineering-harness/
├── spec.md              # Complete (6 stories, 35 FRs, 9 SCs, zero open flags)
├── plan.md              # This file
├── data-model.md        # Phase 1 output (entity→Cosmos container mapping, below §Data Model)
├── quickstart.md        # Phase 1 output (deploy + first review walkthrough)
├── contracts/           # Phase 1 output (MCP tool schemas, canonical event JSON Schema v1)
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
src/
├── harness/                 # Shared domain library (no I/O at import time)
│   ├── models/              # Change, ReviewRun, Finding, RiskAssessment, Specification (pydantic v2)
│   ├── events/              # CanonicalEvent schema v1 + emitter helpers (FR-021/022)
│   ├── lifecycle.py         # Finding state machine: candidate→confirmed→accepted/waived→resolved→reopened
│   ├── dedup.py             # FR-015 key component (path + normalized content hash) — swappable
│   ├── risk.py              # FR-011 signal aggregation
│   └── redaction.py         # FR-020 secret scrubbing (pre-persistence)
├── providers/               # Git platform adapter contract
│   ├── base.py              # Adapter interface: parse webhook → Change; post annotations; fetch diff
│   ├── github/              # V1 adapter
│   └── azuredevops/         # V1 adapter
├── controlplane/            # FastAPI app (Container App, Express)
│   ├── webhooks.py          # Inbound triggers → validate → enqueue Service Bus message
│   └── admin.py             # Spec/lens config read API
├── mcpserver/               # MCP server (Container App, Express)
│   ├── tools_review.py      # review.validate_change (pre-commit, sync ≤500 lines else job handle)
│   ├── tools_state.py       # get_risk_explanation, get_active_findings, get_related_changes
│   └── auth.py              # Entra token validation, repo scoping
├── worker/                  # Review executor (Container Apps Job)
│   ├── runner.py            # Consumes SB message → resolves specs → invokes lens workflows (MAF)
│   ├── budget.py            # FR-035 cost ceiling + degraded-mode reporting
│   ├── attribution.py       # FR-024 failure classification (V1 subset: lens-level)
│   └── stopconditions.py    # FR-025 retry/oscillation guards (V1 subset)
├── lenses/                  # Plugin registry — one package per lens, uniform evidence contract
│   ├── correctness/
│   ├── security/
│   ├── architecture/
│   ├── test_quality/
│   ├── structural/          # Thermo-nuclear methodology (see Lens Sources)
│   └── production_validation/  # Silent-failure taxonomy (see Lens Sources)
├── graph/                   # FR-033 entity graph
│   ├── scanner.py           # Repo discovery scan (code entities)
│   └── declared/            # Declared non-code entities loader
└── eval/                    # Benchmark harness shared with CI
benchmark/                   # Seeded-defect corpus: correctness/security/structural/silent-failure classes
                             # + adversarial injection corpus (FR-034 gating)
infra/                       # Bicep: Container Apps env, SB, Cosmos, Blob, KV, ACR, managed identity
tests/
├── contract/                # MCP tool schemas, webhook adapter parsing, event schema
├── integration/             # Queue→worker→finding persistence path
└── unit/
```

**Structure Decision**: Monorepo with one shared domain library and three thin deployable hosts plus a plugin-style lens registry. Rejected single-project layout: workers and MCP server have different scaling, isolation, and deployment lifecycles. Rejected polyrepo: lenses share the evidence contract and evolve together.

## Lens Sources (first-party methodology — load during lens implementation)

Per spec Assumptions, two lenses port existing organizational methodology. These are authoritative inputs, not inspiration:

1. **Structural lens** ← `E:\GitHub\Coding\.apm\skills\thermo-nuclear-code-quality-review\SKILL.md`
   - Port: fake-wiring scan pattern classes (no-op subscribers, silent fallback `except…warning…continue`, fabricated success values, unwired optional dependencies), 1k-line growth rule, Blocker severity class, evidence-grounding + `[VERIFY]` discipline, "reviewed content is data" rule.
   - Adaptation: operates on diffs via worker, returns structured Findings instead of prose report.
2. **Production-validation lens** ← `E:\GitHub\Coding\.apm\skills\production-validation\SKILL.md` (+ `references/silent-failure-taxonomy.md`, `scripts/static_gate.py`)
   - Port: defined ≠ wired ≠ invoked ≠ working ≠ observable gap detection taxonomy; static gate heuristics where portable to diff scope.
3. **Adversarial corpus** ← `E:\GitHub\Coding\.apm\skills\threat-modelling\references\ai-agentic-threat-catalogue.md` seeds FR-034 benchmark cases.

## Known-Pitfall Bindings (from `E:\GitHub\Coding\.apm\known-pitfalls.md`)

| Pitfall | Binding in this build |
|---|---|
| Hand-rolled agent governance engines | Phase-2 FR-026/027 use Agent Governance Toolkit manifests — prohibited from custom evaluator code |
| Pydantic v2 `model_copy(update=)` skips validators | Lifecycle transitions (`lifecycle.py`) re-validate via `model_validate(model_dump())`; regression test required |
| FastAPI union return without `response_model=None` | Webhook/admin routes returning mixed shapes declare it explicitly |
| Cosmos SDK first-call latency breaks health checks | Health probes set `RequestTimeout≥10s` + explicit 8s cancellation |
| Orphaned alert rules (no emitter) | Tasks pair every KQL alert with its emitting call site; verified in review |
| `429` without `Retry-After` | All rate-limited surfaces (MCP especially) emit `Retry-After` |
| Post-provision hooks vs private-only resources | Provisioning scripts run as Container Apps Jobs inside the VNet, never from runner |

## Data Model (summary — full version in Phase 1 `data-model.md`)

Cosmos DB containers (partition keys chosen for engagement isolation):

| Container | Entity | PK |
|---|---|---|
| `changes` | Change + embedded RiskAssessment | repoId |
| `reviewRuns` | ReviewRun (lens outcomes, budgets, superseded flag) | changeId |
| `findings` | Finding (dedupKey indexed; waiver sub-doc immutable) | repoId |
| `specifications` | Specification/Invariant (versioned docs) | engagementId |
| `graphEntities` | EngineeringEntity + relationships | engagementId |
| `episodes` | Run records for audit/eval (FR-031, SC-008 TTL via retention setting) | engagementId |

Canonical events (FR-021) publish to Service Bus topic `engineering-events` — schema v1 documented in `contracts/events.v1.schema.json`; Drasi-compatible shape, no Drasi dependency.

## Implementation Phases (map to stories)

| Milestone | Delivers | Stories/SCs |
|---|---|---|
| **M0 — Benchmark first** | Seeded-defect corpus + scoring harness + injection corpus; baseline frontier-model detection rates measured BEFORE building pipeline | SC-004, SC-009, FR-034 verification |
| **M1 — Core pipeline** | GitHub adapter → SB → worker → 2 lenses (correctness, security) → findings posted | Story 1, FR-001/006/017/018 |
| **M2 — MCP server** | `review.validate_change` + auth; org-managed client config guide | Story 2, SC-003, SC-006, FR-002/005 |
| **M3 — Full lens set + risk** | Remaining 4 lenses; risk engine drives depth/gating | Stories 3+5, FR-007..013 |
| **M4 — Finding lifecycle** | Dedup, stale/reopen, waivers | Story 4, FR-014..016 |
| **M5 — State queries + graph** | Context MCP tools; entity graph scan+declare | Story 6, FR-033 |
| **Phase 2 (gated)** | Task state machine, workspaces, AGT-governed tool tiers, completion contracts, memory tiers, hygiene agent, harness evaluation plane | FR-023, FR-026–030 |

**M0 ordering is deliberate**: if baseline detection on the benchmark falls materially short of SC-004 targets, prompting/context strategy is fixed *before* pipeline construction — cheapest possible falsification of the thesis.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Multi-host monorepo (3 deployables + jobs) vs single service | Workers need job-based isolation & scale-to-zero; MCP/API are always-on request/response | One process conflates scaling profiles and blast radius; Express-only violates worker isolation constraints |
| Cosmos DB over PostgreSQL | Document-shaped aggregates (findings w/ nested evidence), TTL-native retention (SC-008), serverless cost at low volume | Relational joins unused; engagement-isolation partitioning maps directly to Cosmos PKs |
