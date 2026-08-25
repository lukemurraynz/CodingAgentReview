# Feature Specification: Agentic Engineering Harness (Code Review & Assurance)

**Feature Branch**: `001-agentic-engineering-harness`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "Create a specification of a solution (using Microsoft Foundry / Microsoft Agent Framework and Container Apps Express) for a Code Review Agent/System that can be used with Pull Requests, Commits, and potentially even be called as an MCP server before commit — without having to rely on local skills, GitHub Actions minutes, or Codex/OpenAI credits."

## Overview

The system is a **centrally hosted, vendor-neutral engineering assurance platform** whose first capability is automated code review. It reviews Pull Requests and Commits through configurable **review lenses** (correctness, security, architecture, structural quality, production validation), assigns **continuously computed risk**, manages a full **finding lifecycle**, and exposes all of its intelligence through a **first-class MCP server** so any coding agent (Copilot, Codex, Claude, Cursor, custom) can invoke review *before commit* — with zero local skill installation and zero GitHub Actions or OpenAI/Codex credit consumption.

The platform is organized into four planes:

1. **Engineering Event Plane** — sources (Git host, CI/CD, deployments, incidents, telemetry) emit canonical events.
2. **Change Intelligence** — correlates events into meaningful review/validation triggers and continuously computed risk.
3. **Review / Harness Plane** — Microsoft Agent Framework executes reasoning workflows; Microsoft Foundry supplies model inference; review lenses, specifications, invariants, and verification run here.
4. **Engineering Control Plane** — findings, risk, governance, evaluation, and audit state.

Deliberately out of V1 (designed-for, built-later): Drasi-based continuous queries, autonomous fix/remediation loops together with the harness machinery that serves them (workspace provisioning, tool-tier policy, completion contracts), and any Radius runtime dependency (Radius informs the resource model conceptually only).

**Positioning**: This platform complements rather than replaces built-in PR reviewers such as GitHub Copilot code review. Its differentiation: centrally governed specifications and invariants instead of per-repository prompt tuning; production-validation and structural-scrutiny lenses that generic reviewers do not perform; pre-commit review via MCP before a commit exists; and Git-provider neutrality across heterogeneous environments.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automated PR and Commit Review Without Consuming Platform Credits (Priority: P1)

A developer opens a Pull Request (or pushes a commit). The platform receives the change event through its own webhook endpoint, orchestrates a review using its own hosted model capacity (Microsoft Foundry), and posts structured findings back to the PR. No GitHub Actions workflow runs; no repository-hosted skills, prompts, or configuration are required; the developer's OpenAI/Codex account is never touched.

**Why this priority**: This is the core product promise. Everything else exists to serve it. If reviews don't run cheaply, centrally, and reliably on every change, there is no product.

**Independent Test**: Can be fully tested by opening a PR containing a known defect and verifying findings appear on the PR within the target latency, while confirming zero Actions runs were triggered and no external model credentials were used.

**Acceptance Scenarios**:

1. **Given** a configured repository, **When** a PR is opened, **Then** a review run starts automatically within 30 seconds and findings are posted to the PR.
2. **Given** a direct push to a protected branch, **When** the commit lands, **Then** a commit-scoped review runs and results are recorded and surfaced.
3. **Given** a repository with no local agent configuration whatsoever, **When** a PR is opened, **Then** review still executes using centrally managed specifications and lenses.
4. **Given** 100 PRs opened concurrently across repositories, **When** all are queued, **Then** all receive reviews with no dropped requests and bounded queue delay.

---

### User Story 2 - Pre-Commit Review Via MCP Server From Any Coding Agent (Priority: P2)

A developer is working in any MCP-capable coding agent. Before committing, the agent calls the platform's MCP server (e.g., `review.validate_change` with the working diff) and receives structured findings plus a risk assessment inline — before the code ever leaves the developer's machine as a commit. Because the intelligence lives server-side behind MCP, the developer installs nothing locally.

**Why this priority**: This is the key differentiator versus every review bot: shifting review left of the commit, agent-agnostic by design, and the mechanism that makes "no local skills" true rather than aspirational.

**Independent Test**: Can be fully tested by connecting any stock MCP client to the published server, submitting a diff containing a known defect, and verifying structured findings return without any local configuration.

**Acceptance Scenarios**:

1. **Given** an authenticated MCP session, **When** the agent submits a working-tree diff, **Then** structured findings with severity, location, rationale, and suggested fix return synchronously (or via job handle if async).
2. **Given** two different agents (e.g., Copilot and Claude Code) calling the identical MCP tool with identical input, **When** results return, **Then** both receive equivalent structured output.
3. **Given** an unauthenticated MCP request, **When** submitted, **Then** the request is rejected with a clear auth error and nothing executes.

---

### User Story 3 - Risk-Proportionate Review Depth (Priority: P3)

Not every change deserves the same scrutiny. The platform computes a risk assessment per change from signals such as affected security boundaries, public API surface changes, production deployment targets, test coverage deltas, ownership, and recent incident history. Risk selects which lenses run, how deep they go, and whether human approval gates apply. Risk is exposed as queryable state, not buried in a log line.

**Why this priority**: Cost and signal-to-noise determine whether teams trust the reviewer. Uniform-depth review is either too expensive or too shallow; risk proportionality is what makes Story 1 sustainable at scale.

**Independent Test**: Can be fully tested by submitting a docs-only change and an identity-boundary Terraform change and verifying the latter triggers deeper lenses and higher reported risk.

**Acceptance Scenarios**:

1. **Given** a change touching only Markdown, **When** reviewed, **Then** minimal lenses execute and risk reports low.
2. **Given** a change modifying authorization code with no corresponding test, **When** reviewed, **Then** risk reports high, the security lens runs, and a blocking finding category is applied.
3. **Given** a change to infrastructure targeting production with a failed CI check, **When** evaluated, **Then** risk escalates to critical and the review result requires human acknowledgement before merge-gating passes.

---

### User Story 4 - Finding Lifecycle With Staleness and Reopening (Priority: P4)

Findings are durable objects, not ephemeral PR comments. Each finding moves through Candidate → Confirmed → Accepted (waived with justification) → Resolved, and — critically — can be **Reopened** when subsequent events indicate the underlying issue resurfaced or a related semantic code path changed again. Stale findings auto-update when the diff they referenced no longer exists.

**Why this priority**: Reviews die as noise unless findings persist, deduplicate across pushes, and survive PR churn. Lifecycle management is what turns a comment bot into an assurance system.

**Independent Test**: Can be fully tested by opening a PR with a defect, pushing a "fix", then reintroducing the same defect, and verifying the original finding reopens rather than spawning an orphan duplicate.

**Acceptance Scenarios**:

1. **Given** a confirmed finding on PR #10, **When** force-push removes the offending code, **Then** the finding transitions to stale/resolved-by-change rather than lingering open.
2. **Given** a resolved finding, **When** a later commit reintroduces the same semantic issue on the same path, **Then** the original finding reopens with linkage to the new commit instead of duplicating.
3. **Given** a team lead reviewing a low-severity finding, **When** they waive it with a documented reason, **Then** the waiver is recorded immutably in audit state with author, timestamp, and rationale.

---

### User Story 5 - Structured Quality Lenses Including Production Validation and Structural Scrutiny (Priority: P5)

Reviews are composed from independently executable **lenses**: correctness, security, architecture, test quality, **structural over-engineering scrutiny** (detects unnecessary abstraction layers, speculative flexibility, dead configuration — evidenced by change metrics, not vibes), and **production validation** (verifies declared ≠ wired ≠ invoked ≠ working ≠ observable gaps: e.g., a new service registered but never called, a fallback path that cannot activate, a feature missing telemetry).

**Why this priority**: Lens composition is the extensibility backbone and encodes the organization's hardest-won lessons, but stories 1–4 must exist for any lens output to reach anyone.

**Independent Test**: Can be fully tested by seeding a repository with a known silent-failure pattern (component added, no telemetry, no caller) and verifying the production-validation lens flags it specifically.

**Acceptance Scenarios**:

1. **Given** a diff introducing a third wrapper layer between API and persistence with no new reusable boundary, **When** the structural lens runs, **Then** a finding cites concrete added-node/edge evidence.
2. **Given** a new public API endpoint with changed authorization logic and no authorization test, **When** the security lens runs, **Then** a blocking finding identifies the missing invariant coverage.
3. **Given** a lens that throws an internal error mid-review, **When** the run completes, **Then** remaining lenses still report and the failure is recorded against that lens only — the whole run does not silently pass or fail.

---

### User Story 6 - Engineering State Exposed As Queryable Context (Priority: P6)

Any agent (including the platform's own) can ask the MCP server questions like *"why is this PR high risk?"*, *"what active findings affect this service?"*, *"what related changes exist?"* and receive grounded answers assembled from platform state — rather than each agent independently reconstructing context from Git history.

**Why this priority**: This converts the platform from a gatekeeper into shared engineering memory, and it is the substrate the later event-intelligence evolution plugs into. Valuable, but dependent on Stories 1–4 producing that state first.

**Independent Test**: Can be fully tested by generating a reviewed PR with known risk drivers, querying the context MCP tool for that PR, and verifying the response enumerates exactly those drivers.

**Acceptance Scenarios**:

1. **Given** a PR previously assessed high-risk due to identity-boundary changes, **When** an agent queries risk explanation for that PR, **Then** the response lists identity-boundary change among the cited reasons.
2. **Given** a query for active findings on a service, **When** executed, **Then** only non-stale, non-waived findings return, each linked to source change.

---

### Edge Cases

- What happens when a PR is updated while a review is mid-flight? (Result must be marked superseded/stale against the new head SHA — never posted as current.)
- How does the system handle enormous diffs (thousands of files)? Must chunk/degrade gracefully with explicit partial-coverage reporting, never silently truncate.
- How does the system treat non-code changes (docs-only, lockfile-only, generated code)? Classified and routed to proportionate handling.
- What happens when the model backend is unavailable or quota-exhausted? Requests queue durably; deterministic gates still run; explicit degraded-mode status is visible — never fabricated results.
- What happens when two specifications conflict on the same change? Conflict surfaces as a governance finding, not arbitrary precedence.
- What happens when a diff contains secrets? Findings must not echo secret values; redaction applies everywhere including audit logs.
- What happens when a repository is deleted or made private mid-review? In-flight work aborts cleanly; no orphaned artifacts retain access.
- What happens when the agent's tool fails repeatedly during any autonomous operation? Stop conditions trigger escalation, not infinite retry.

## Requirements *(mandatory)*

### Functional Requirements

**Triggers & Interfaces**

- **FR-001**: System MUST accept change triggers via inbound webhooks from supported Git platforms for pull-request opened/updated and push/commit events.
- **FR-002**: System MUST expose review, risk, findings, specifications, and engineering-state capabilities through an MCP server usable by any conformant client.
- **FR-003**: System MUST operate with **zero** repository-local installation: no required local skills, prompt files, agents, or CI workflow definitions in consumer repositories.
- **FR-004**: System MUST NOT consume GitHub Actions minutes, nor customer-held OpenAI/Codex credentials, for any review operation; all inference runs on platform-hosted Microsoft Foundry capacity and MUST NOT be locked to a single model family — lenses select among Foundry-hosted models from multiple providers per deployment configuration.
- **FR-005**: System MUST support synchronous MCP invocation of review against an ad-hoc diff (pre-commit flow) in addition to event-triggered review.

**Review Execution**

- **FR-006**: System MUST execute reviews as composable, independently runnable review lenses selected per change.
- **FR-007**: System MUST include, at minimum: correctness, security, architecture, test-quality, structural-scrutiny, and production-validation lenses.
- **FR-008**: System MUST resolve centrally governed specifications and invariants and evaluate them per change, with applicability determined semantically where possible (by affected component/resource type) rather than purely by LLM prose interpretation.
- **FR-009**: Lens failures MUST be isolated: a failing lens records its error and cannot corrupt or suppress other lenses' results.
- **FR-010**: System MUST attach evidence (diff locations, rule/spec identifiers, measured metrics) to every finding; unsupported assertions MUST be marked as such.

**Risk & Gating**

- **FR-011**: System MUST compute a per-change risk assessment from defined signals (security-boundary impact, public surface change, deployment target, test delta, incident history) and record it as queryable state.
- **FR-012**: Risk level MUST influence lens selection, review depth, and whether human approval is required before merge-gating passes.
- **FR-013**: System MUST support merge-block on blocking-category findings per repository policy.

**Findings**

- **FR-014**: System MUST persist findings with lifecycle states Candidate → Confirmed → Accepted/Waived → Resolved → Reopened, including stale detection on diff change.
- **FR-015**: System MUST deduplicate findings across successive commits of the same PR using file-path + normalized content-hash matching; the dedup key MUST be an isolated component so stronger matching (symbol-level, then AST-based) can replace it later without schema or lifecycle changes.
- **FR-016**: Waivers MUST capture approver identity, timestamp, and rationale, and MUST be immutable once written.

**Platform Constraints**

- **FR-017**: All long-running orchestration MUST use durable queuing between trigger and worker such that no review is lost to transient worker failure.
- **FR-018**: All state (findings, risk, runs, waivers, episodes) MUST persist in platform-owned storage; the Git host holds comments/annotations only as projections.
- **FR-019**: All access MUST authenticate via organizational identity; per-repository scoping MUST be enforced end-to-end including MCP tool invocations.
- **FR-020**: Secrets detected in diffs or logs MUST be redacted before persistence or projection.

**Event Model (design-now, expand-later)**

- **FR-021**: System MUST define and emit a canonical event set — at minimum: PullRequestChanged, CommitCreated, BuildCompleted, TestCompleted, DeploymentCompleted, FindingCreated, FindingResolved, RuntimeSignalObserved, IncidentOpened, SpecificationChanged — on a durable event stream, so future continuous-query (change-intelligence) consumers can subscribe without redesign.
- **FR-022**: Event payloads MUST be versioned and schema-documented.

**Agent Operations (harness behaviors)**

*Scope note: only FR-024 and FR-025 are in V1 — review-lens execution needs failure attribution and stop conditions regardless of autonomy level. FR-023 and FR-026–FR-030 serve autonomous remediation, which has no user story in this specification; they gate the Harness Operations milestone.*

- **FR-023 [Phase 2]**: Any multi-step agentic operation MUST track an explicit task state machine (created → understanding → planning → implementing → validating → reviewing → verifying → ready-for-human → merged) with defined recovery states (blocked, waiting-for-human, waiting-for-environment, failed, abandoned).
- **FR-024**: Every failed step in an agentic operation MUST be classified by failure attribution (agent error, product defect, environment error, test defect, dependency error, unknown) before retry decisions.
- **FR-025**: Agentic operations MUST enforce stop conditions — including N consecutive identical failures, oscillation detection, budget exhaustion, unresolved specification, and repeated tool failure — transitioning to STOP/ESCALATE rather than retrying indefinitely.
- **FR-026 [Phase 2]**: Tool availability for agentic operations MUST be governed by a risk-tiered policy (read-only tier always available; mutation tiers gated progressively; production-affecting tier requires explicit human approval).
- **FR-027 [Phase 2]**: Agents MUST NEVER hold standing broad credentials; all external-system access MUST flow through brokered, least-privilege, task-scoped identities.
- **FR-028 [Phase 2]**: Each agentic execution MUST run in an isolated, ephemeral workspace provisioned from a versioned environment recipe, torn down after the task, with recorded environment manifest for reproducibility.
- **FR-029 [Phase 2]**: Task completion MUST be declared only when a defined completion contract is satisfied (build passes, tests pass, applicable specs pass, no blocking findings, clean workspace state) — model message termination alone is insufficient.
- **FR-030 [Phase 2]**: Memory MUST be maintained in three isolated scopes — task-scoped, repository-scoped, organization-scoped — with repository/organization scopes read-authoritative and never polluted by single-task state.

**Governance & Evolution**

- **FR-031**: Every review run and agentic episode MUST produce a queryable record (inputs hash, decisions, tool usage, outcomes) sufficient for evaluation and audit.
- **FR-032**: Changes to lenses, specifications, policies, or harness behavior MUST themselves go through review and MUST be evaluated against historical episode data before promotion.
- **FR-033**: System MUST maintain a semantic model of engineering entities (repositories, services, APIs, agents, tools, specifications, environments) with typed relationships, supporting applicability resolution and impact queries. Code-level entities are populated by repository discovery scanning; non-code entities (specifications, teams, environments, deployments) are declared; both sources merge into one graph.

### Adversarial Input & Cost Governance

- **FR-034**: System MUST treat all reviewed content — code, diffs, file contents, commit messages, PR descriptions — as untrusted input. Prompt injection embedded in reviewed content MUST NOT alter review outcomes, suppress findings, or fabricate passing results; lens outputs are evidence evaluated by policy, never direct triggers for actions.
- **FR-035**: System MUST enforce a configurable per-review cost ceiling (model token/compute budget). On exhaustion, the run MUST complete with partial coverage explicitly reported as degraded — never silently truncated and never fabricated.

### Key Entities *(include if feature involves data)*

- **Change**: A unit under review (PR, commit, ad-hoc diff); attributes: id, head SHA, base, files touched, classification.
- **ReviewRun**: One execution of the pipeline against a Change; lenses invoked, durations, outcomes, superseded flag.
- **Lens**: Named review capability with its own prompt/policy/evidence contract; versioned independently.
- **Finding**: Durable issue with severity, category, lifecycle state, evidence links, dedup key, waiver record.
- **RiskAssessment**: Per-change risk vector (per-domain levels + triggering signals) with computation provenance.
- **Specification / Invariant**: Centrally governed intent statements and mechanically-checkable invariants with applicability metadata.
- **EngineeringEntity & Relationship**: Nodes/edges of the semantic engineering graph (service depends-on datastore, API owned-by team, spec applies-to resource type).
- **CanonicalEvent**: Versioned event emitted on the platform event stream per FR-021.
- **WorkspaceSession**: Ephemeral isolated execution context with recipe reference and environment manifest.
- **EpisodeRecord**: Auditable trace of one agentic operation including state transitions, attributions, and stop-condition evaluations.
- **Environment**: Named execution context (local/ci/sandbox/integration/staging/production-read-only/production-controlled) defining permitted tools, credential scope, network posture, and approval requirements.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: ≥95% of PR-open events produce a completed review posting within 5 minutes at p95 under normal load.
- **SC-002**: Zero GitHub Actions minutes and zero third-party model credits consumed across a full month of production review traffic (verified by billing/export evidence).
- **SC-003**: Pre-commit MCP review round-trip completes in under 60 seconds for diffs up to 500 changed lines at p95.
- **SC-004**: On a seeded benchmark suite of known defects (correctness, security, structural, silent-failure classes), lenses detect ≥80% of planted issues with ≤15% false-positive rate on clean code.
- **SC-005**: ≥90% of reintroduced defects attach to the pre-existing finding (reopen) rather than creating duplicates, measured on a 30-day window.
- **SC-006**: A developer with a fresh machine and stock coding agent completes a pre-commit review flow with zero local setup steps beyond authentication.
- **SC-007**: 100% of production-affecting agentic actions in any episode trace show prior recorded human approval.
- **SC-008**: Every review run is reconstructable from persisted state (inputs, versions of lenses/specs/models used, outputs) for a configurable retention period, defaulting to 12 months per deployment.
- **SC-009**: A seeded defect benchmark suite (correctness, security, structural, silent-failure classes) exists and executes in CI as a launch-blocking deliverable; no lens, model, or prompt change ships if it regresses detection below SC-004 thresholds on the current corpus.

## Assumptions

- **V1 stack**: Microsoft Foundry (inference), Microsoft Agent Framework (orchestration/harness primitives), Azure Container Apps hosting — Express profile acceptable for control-plane/MCP/orchestrator components, but agent workspace execution uses isolated jobs with filesystem/process/network isolation (Express constraints acknowledged and respected).
- Supporting services assumed available: durable messaging (Service Bus-class), document storage (Cosmos DB-class), blob storage for large artifacts, Application Insights telemetry, Entra ID, Key Vault.
- **Drasi-style continuous change-intelligence is intentionally deferred** until at least three genuine event-correlation requirements emerge that are awkward in ordinary application code; V1 ships the canonical event model (FR-021) so introduction requires no redesign.
- **Radius is a conceptual donor, not a dependency**: resource types, recipes, environments, connections, and desired-vs-observed patterns are adopted as internal design vocabulary; no Radius control plane is deployed.
- Trigger integration is abstracted behind a provider adapter; V1 ships adapters for both GitHub and Azure DevOps (both named in the event-plane design). Additional providers (Bitbucket, self-hosted GitLab) plug in via the same adapter contract without core changes.
- Existing organizational methodologies ("production validation" and "structural quality review") are incorporated as first-party lens definitions, not external integrations.
- Single-organization deployment initially; SaaS-scale multi-tenancy is out of scope for V1. However, when deployed as an accelerator into distinct customer engagements (e.g., forward-deployed engineering delivery), specification registries, organizational memory, and learned intelligence MUST be isolated per engagement so one customer's encoded intelligence never influences another's deployment.
