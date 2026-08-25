# Phase-2 Non-Goals Register

This register documents capabilities that are explicitly deferred to Phase 2. Each entry records what is deferred, why it was deferred from V1, and the trigger condition that would move it into active scope.

Source: tasks.md T080 — FR-023, FR-026, FR-027, FR-028, FR-029, FR-030.

---

## NG-01: AGT governance manifests (FR-026 — tool-tier policy)

**What**: A risk-tiered tool availability policy for agentic operations. Three tiers: read-only (always available), mutation (progressively gated), production-affecting (requires explicit human approval). Governed by a declarative manifest that defines which tools belong to which tier per environment.

**Why deferred**: V1 has no autonomous remediation or fix-application loop. The only agentic actions in V1 are review lens execution and finding lifecycle transitions, both of which are narrow and fully supervised. Building a governance manifest layer on top of zero autonomous actions creates infrastructure with no current consumer and a high risk of designing it for the wrong shape of problem before real Phase-2 use cases are known.

**What V1 ships instead**: Stop conditions (`src/worker/stopconditions.py`) and failure attribution (`src/worker/attribution.py`) cover the failure-containment surface that matters for review lens execution (FR-024/025 V1 subset). The critical-risk acknowledgement gate (`src/worker/gate.py`) blocks production-affecting merge-gate changes without a tool-tier policy layer.

**Revisit trigger**: When a second agentic action type beyond review execution is implemented — specifically any action that writes to a system outside the harness's own state store (Cosmos, Service Bus) — the governance manifest must be designed and deployed before that action ships.

---

## NG-02: Ephemeral workspace provisioning (FR-028 — isolated execution environments)

**What**: Each agentic execution runs in an isolated, ephemeral workspace: a versioned environment recipe, a provisioned compute context with filesystem/process/network isolation, a recorded environment manifest for reproducibility, and automatic teardown after the task.

**Why deferred**: V1 workers are stateless Container App jobs that process one Service Bus message per invocation and terminate. They have no persistent workspace state and no need to provision or tear down an environment per task. The isolation Container Apps jobs provide is sufficient for review-only workloads. Workspace provisioning machinery is expensive to build and maintain correctly; building it before there are autonomous operations that require it (fix branches, test runs, deployment probes) would produce premature infrastructure.

**What V1 ships instead**: Worker jobs run in Container Apps with per-invocation isolation. The spec explicitly flags Express profile constraints as acknowledged (spec.md Assumptions). `WorkspaceSession` is defined as a domain entity in the spec for future use.

**Revisit trigger**: When the harness needs to execute code — running tests, applying a generated fix, invoking a deployment probe — workspace provisioning becomes necessary. The trigger is the first Phase-2 user story that requires writing code and verifying it executes.

---

## NG-03: Memory tiers — repository-scoped and organization-scoped (FR-030)

**What**: Memory maintained in three isolated scopes: task-scoped (ephemeral), repository-scoped (read-authoritative across tasks on the same repo), and organization-scoped (cross-repo intelligence, never polluted by single-task state). Explicit isolation rules prevent task-scoped state from leaking into higher scopes.

**Why deferred**: V1 has no persistent agent memory. Review runs are stateless: they read specifications and findings from Cosmos but do not write learned inferences back to a scoped memory store. Designing a three-tier memory system before there are multi-step agentic operations that produce inter-task learnings would mean designing for a hypothetical access pattern. The per-engagement isolation requirement (spec.md Assumptions) is already satisfied at the Cosmos partition level without a memory-tier abstraction.

**What V1 ships instead**: Findings, risk assessments, and run records in Cosmos serve as the durable engineering state that the MCP `get_active_findings`/`get_risk_explanation` tools read. This is read-only shared context, not writable memory.

**Revisit trigger**: When a Phase-2 agent needs to persist a learned inference (e.g., "this service has historically had authorization boundary issues") and have it inform future reviews of the same repo, memory tiers become necessary. The trigger is the first use case where an agent writes something more specific than a Finding to shared state.

---

## NG-04: Task state machine for agentic operations (FR-023)

**What**: An explicit state machine per multi-step agentic operation: `created → understanding → planning → implementing → validating → reviewing → verifying → ready-for-human → merged`, with defined recovery states (`blocked`, `waiting-for-human`, `waiting-for-environment`, `failed`, `abandoned`).

**Why deferred**: V1 review runs follow a simple linear path: receive message, run lenses, persist findings, post annotation. There are no branching states, no waiting-for-environment pauses, and no human-in-the-loop steps within the run itself (human approval is an external gate, not a state in the runner). A full state machine adds overhead and complexity with no V1 behavior to attach it to.

**What V1 ships instead**: `ReviewRun` has a `status` field covering the review-specific lifecycle. Stop conditions (`src/worker/stopconditions.py`) handle failure containment. FR-024/025 failure attribution and stop conditions are implemented as a V1 subset.

**Revisit trigger**: When a Phase-2 operation needs to pause mid-execution waiting for an environment event (test results, deployment probe, human approval of an intermediate artifact), the state machine becomes necessary to track and resume safely.

---

## NG-05: Hygiene agent (autonomous fix and remediation loop)

**What**: An agent that takes confirmed findings and autonomously proposes or applies fixes — opening branches, running tests, verifying the fix satisfies the relevant specification, and requesting merge review. This includes the completion contract mechanism (FR-029): task completion declared only when build passes, tests pass, applicable specs pass, no blocking findings, and workspace is clean.

**Why deferred**: This capability requires all of NG-01 through NG-04 to be in place and stable first. Autonomous remediation without tool-tier governance, workspace isolation, memory tiers, and a proper task state machine is unsafe by design. The spec is explicit: *"autonomous fix/remediation loops together with the harness machinery that serves them... are deliberately out of V1"* (spec.md Overview). Building the hygiene agent before that foundation is ready risks shipping an agent that has unconstrained write access, no provenance trail, and no principled stop condition.

**What V1 ships instead**: The finding lifecycle (`candidate → confirmed → waived → resolved`) and the admin waiver API (`src/controlplane/admin.py`) provide the human-in-the-loop path for acting on findings. Developers receive structured findings with evidence and resolve them manually.

**Revisit trigger**: When NG-01 through NG-04 are all deployed and validated in production, and when at least one Phase-2 user story explicitly requires autonomous code modification. The hygiene agent should not ship until the governance manifest (NG-01) can gate its production-affecting tool access.

---

## NG-06: Brokered task-scoped identity (FR-027)

**What**: Agents must never hold standing broad credentials. All external-system access flows through brokered, least-privilege, task-scoped identities provisioned per operation and revoked on completion.

**Why deferred**: V1 agents (review workers) access only the harness's own Service Bus, Cosmos, Blob, and Foundry resources using a single managed identity granted the minimum roles needed by `infra/scripts/grant-access.ps1`. There is no agent that accesses external systems on behalf of a developer or organization. The threat that FR-027 protects against — an agent with standing credentials to a customer's production database — does not exist in V1.

**What V1 ships instead**: Managed identity with role-based access to platform-owned resources only. No customer-system credentials are held by any V1 component.

**Revisit trigger**: When a Phase-2 agent needs to read from or write to a system outside the harness (a customer's repository, a deployment system, a test runner), brokered task-scoped identity becomes mandatory before that capability ships.
