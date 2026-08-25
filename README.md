# CodingAgentReview — Agentic Engineering Harness

Centrally hosted **code review & engineering assurance platform**: PR/commit reviews through composable lenses, risk-proportionate depth, durable finding lifecycle, and a vendor-neutral **MCP server** for pre-commit review from any coding agent — zero repository-local install, zero GitHub Actions minutes, zero customer-held model keys.

Built on Microsoft Foundry (model-router) + Agent Framework patterns + Azure Container Apps.

## Live deployment (harness-dev)

| Service | URL |
|---|---|
| controlplane | https://ca-harness-dev-lm-controlplane.bravesky-4430540a.swedencentral.azurecontainerapps.io |
| mcpserver | https://ca-harness-dev-lm-mcpserver.bravesky-4430540a.swedencentral.azurecontainerapps.io/mcp |

## How it works

```
Git webhook ─▶ controlplane ─▶ Service Bus ─▶ worker (Container App, KEDA)
                                   │                ├─ deterministic lenses (structural, production_validation)
                                   │                ├─ LLM lenses via Foundry model-router
                                   │                └─ findings/risk ─▶ Cosmos DB ─▶ posted back to PR
any coding agent ──────────▶ mcpserver (/mcp) ◀── query state / pre-commit review_validate_change
```

- **Risk-proportionate depth**: docs-only changes skip deep review; identity/production changes escalate.
- **Findings persist** (`candidate→confirmed→waived→resolved→reopened`), dedupe across commits, reopen on reintroduction.
- **Cost ceiling + stop conditions** per run; degraded runs are explicitly marked, never silent.
- Full spec: [`specs/001-agentic-engineering-harness/spec.md`](specs/001-agentic-engineering-harness/spec.md) · architecture: [`plan.md`](specs/001-agentic-engineering-harness/plan.md)

## Deploy

```powershell
azd init --environment harness-dev --location swedencentral
azd env set AZURE_SUBSCRIPTION_ID <sub-id>
azd env config set infra.parameters.namePrefix harness-dev-lm
azd env config set infra.parameters.githubWebhookSecret <secret>
azd up        # provision + build (ACR remote — no local Docker needed) + deploy + grants
```

Access grants (SB/Blob/ACR/Cosmos/Foundry for the managed identity) run automatically post-provision via `infra/scripts/grant-access.ps1`.

## Configuration

All `HARNESS_*` env vars are optional unless noted:

| Var | Used by | Purpose |
|---|---|---|
| `HARNESS_GITHUB_WEBHOOK_SECRET` 🔒 | controlplane | HMAC validation (required for GH triggers) |
| `HARNESS_GITHUB_TOKEN` | providers | private-repo diff fetch / comments |
| `HARNESS_ADO_PAT` 🔒 | providers | Azure DevOps auth |
| `HARNESS_SERVICEBUS_NS` / `_LISTEN_CONN` 🔒 | controlplane / worker | queue send/receive |
| `HARNESS_COSMOS_ENDPOINT`, `HARNESS_COSMOS_DATABASE` | worker, mcpserver | state store |
| `HARNESS_BLOB_ENDPOINT` | worker | evidence artifacts |
| `HARNESS_FOUNDRY_ENDPOINT` (+`_DEPLOYMENT`) | worker, lenses | Foundry model-router (Entra auth; key fallback for local dev) |
| `HARNESS_BUDGET_{INPUT_TOKENS,OUTPUT_TOKENS,COMPUTE_MS}` | worker | per-review cost ceiling (FR-035) |
| `HARNESS_DRY_RUN`, `HARNESS_JOB_MODE`, `HARNESS_DRAIN_SECONDS` | worker | ops toggles |

🔒 = secret; in Azure these live in Container App secrets, never in git.

## Develop & test

```powershell
uv sync --extra dev --extra azure --extra api --extra mcp
uv run pytest          # unit + contract suites
uv run ruff check .
uv run python scripts/run_baseline.py   # gate-zero benchmark vs Foundry
```

Benchmark results: [`docs/benchmark-baseline.md`](docs/benchmark-baseline.md) · methodology learnings: [`docs/harness-learnings.md`](docs/harness-learnings.md)
