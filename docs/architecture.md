# Architecture

The harness reviews code changes through composable lenses and keeps every finding in a durable, auditable lifecycle.

## Component view

```mermaid
flowchart TB
    subgraph Intake
        GH[GitHub / ADO webhook] --> CP[Control plane<br/>HMAC verify · dedup lock<br/>risk classification]
    end

    CP --> SB[(Service Bus<br/>review-requests)]
    SB --> W[Worker<br/>KEDA-scaled]

    subgraph Pipeline["Review pipeline (worker)"]
        direction TB
        FETCH[Fetch diff] --> DEPTH[Risk-depth policy<br/>monotonic floor]
        DEPTH --> LENSES[Lens set:<br/>structural · production_validation ·<br/>architecture · test_quality ⚙️<br/>correctness · security 🤖]
        LENSES --> SUPPRESS[Cross-lens suppression]
        SUPPRESS --> EXPL[Exploitability triage<br/>security findings]
        EXPL --> SECOND[Worst-of-N second opinion<br/>on blocking findings]
        SECOND --> GATE[Gate verdict<br/>pass / block + coverage line]
    end

    W --> DB[(Cosmos DB<br/>findings · runs · episodes)]
    W --> BLOB[(Blob evidence)]
    GATE -- upsert PR comment --> GH

    DEV[Coding agent] -- Entra JWT --> MCP[MCP server<br/>review.validate_change<br/>fix.propose · state queries]
    MCP --> PIPE[Same lens pipeline,<br/>inline - never queued]
    MCP --- DB

    ADMIN[Admin API<br/>waivers · specifications] --> CP
```

## Sequence: automatic PR review

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub/ADO
    participant CP as Control plane
    participant Q as Service Bus
    participant W as Worker
    participant F as Foundry
    participant DB as Cosmos DB

    Dev->>GH: open PR
    GH->>CP: webhook (HMAC-signed)
    CP->>CP: dedup (repo, pr, sha) · classify risk
    CP->>Q: enqueue CanonicalEvent
    Q->>W: deliver (KEDA scales 0→1)
    W->>GH: fetch diff
    W->>W: deterministic lenses (structural, prodval, architecture, test-quality)
    W->>F: correctness + security lens prompts (+ trusted repo index & rule briefs)
    F-->>W: JSON findings
    W->>W: suppress duplicates · exploitability triage · second opinion on blockers
    W->>DB: persist findings (dedupe → confirmed / stale / reopened)
    W->>GH: single dashboard comment + verdict
```

## Finding lifecycle

```mermaid
stateDiagram-v2
    [*] --> candidate: first seen
    candidate --> confirmed: still present on re-review
    confirmed --> resolved: fix lands / region removed (stale)
    resolved --> reopened: dedupe key reappears later
    confirmed --> waived: admin waiver (immutable, attributed)
    waived --> [*]: terminal unless un-waived by policy
```

- **Dedupe keys** (`path` + normalized content hash) identify "the same problem" across commits.
- **Reopen** links a reintroduced finding back to its original — history is never rewritten.
- **Waivers** are enforced immutable at both the state-machine and repository layers.

## Trust boundaries

| Input | Treatment |
|---|---|
| Diff content | Untrusted data — prompt-injection attempts are themselves findings |
| `.harness/rules/*.md`, org specs | Trusted maker-authored context (version-controlled in the repo) |
| Repo symbol index | Trusted generated context, bounded and labeled before entering prompts |
| Webhooks | HMAC-verified; redelivery-deduped |
| MCP requests | Entra JWT (JWKS-pinned), repo-scoped via `repos` claim or app roles |

Deterministic enforcement (auth, tenancy, budgets, dead-lettering) lives outside the prompts — instructions are defence-in-depth only.

## Key source map

| Area | Path |
|---|---|
| Domain models & lifecycle | `src/harness/` |
| Lenses | `src/lenses/` |
| Review orchestration | `src/worker/` |
| Providers (GitHub, Azure DevOps) | `src/providers/` |
| Webhook intake + admin API | `src/controlplane/` |
| MCP server | `src/mcpserver/` |
| Spec/rule discovery & applicability | `src/graph/` |
| Benchmark & scoring | `src/eval/`, `benchmark/` |
