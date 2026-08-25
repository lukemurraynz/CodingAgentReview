# Quickstart: Agentic Engineering Harness

This guide takes you from zero to a working PR review and your first pre-commit MCP call.

---

## 1. Deploy to Azure ☁️ *(requires live Azure)*

You need:
- An Azure subscription
- `azd` CLI installed (`winget install Microsoft.Azd` or `brew install azure/azd/azd`)
- `az login` completed in the same shell

```powershell
azd init --environment harness-dev --location swedencentral
azd env set AZURE_SUBSCRIPTION_ID <your-subscription-id>
azd env config set infra.parameters.namePrefix harness-dev-lm
azd env config set infra.parameters.githubWebhookSecret <random-32-char-secret>
azd up
```

`azd up` provisions all Azure resources (Container Apps, Service Bus, Cosmos DB, ACR, Blob, Foundry), builds container images via ACR remote build (no local Docker needed), deploys, and runs `infra/scripts/grant-access.ps1` to wire managed-identity RBAC for Service Bus, Blob, ACR, Cosmos, and Foundry automatically.

After completion, `azd` prints the two service URLs:

| Service | URL pattern |
|---|---|
| controlplane | `https://ca-<prefix>-controlplane.<env>.azurecontainerapps.io` |
| mcpserver | `https://ca-<prefix>-mcpserver.<env>.azurecontainerapps.io/mcp` |

---

## 2. Configure required secrets ☁️ *(requires live Azure)*

All secrets go into Container App secrets — never into git. Use `azd env set` or the Azure portal.

| Secret | Container App | Purpose |
|---|---|---|
| `HARNESS_GITHUB_WEBHOOK_SECRET` | controlplane | HMAC-SHA256 signature validation on every GitHub webhook delivery |
| `HARNESS_MCP_ENTRA_TENANT_ID` | mcpserver | Entra tenant ID for Bearer JWT validation on `/mcp`; auth fails closed if unset |
| `HARNESS_MCP_ENTRA_AUDIENCE` | mcpserver | Expected `aud` claim on MCP tokens (e.g., `api://agentic-harness`) |
| `HARNESS_ADMIN_TOKEN` | controlplane | Shared-secret bearer auth for `/admin/*` waiver and specification APIs |
| `HARNESS_FOUNDRY_ENDPOINT` | worker | Azure AI Foundry model-router endpoint URL |

Optional but useful:

| Var | Purpose |
|---|---|
| `HARNESS_GITHUB_TOKEN` | Private-repo diff fetch and PR comment posting |
| `HARNESS_ADO_PAT` | Azure DevOps webhook auth |
| `HARNESS_MCP_ENTRA_CLIENT_ID` | Additional accepted `aud` value for MCP tokens |

Set via `azd`:

```powershell
azd env set HARNESS_MCP_ENTRA_TENANT_ID  <your-tenant-guid>
azd env set HARNESS_MCP_ENTRA_AUDIENCE   api://agentic-harness
azd env set HARNESS_ADMIN_TOKEN          <strong-random-secret>
azd env set HARNESS_FOUNDRY_ENDPOINT     https://<foundry-resource>.services.ai.azure.com/models
azd deploy   # push updated env vars
```

---

## 3. Connect a webhook ☁️ *(requires live Azure)*

### GitHub

1. Repository → **Settings** → **Webhooks** → **Add webhook**
2. **Payload URL**: `https://ca-<prefix>-controlplane.<env>.azurecontainerapps.io/webhook/github`
3. **Content type**: `application/json`
4. **Secret**: the value you set for `HARNESS_GITHUB_WEBHOOK_SECRET`
5. **Events**: select **Pull requests** and **Pushes**

### Azure DevOps

1. Project → **Project Settings** → **Service hooks** → **+** → **Web Hooks**
2. **Trigger**: `Pull request created` (add a second hook for `Pull request updated`)
3. **URL**: `https://ca-<prefix>-controlplane.<env>.azurecontainerapps.io/webhook/azuredevops`
4. Set a shared secret and configure `HARNESS_ADO_PAT` accordingly

---

## 4. Trigger your first PR review ☁️ *(requires live Azure)*

Open a pull request in a connected repository. Within 30 seconds the worker picks the job from Service Bus and posts a structured comment to the PR.

### What the PR comment contains

```
## Harness Review — risk: medium | verdict: pass

lenses: 3 declared, 3 reported, 0 unavailable

| Severity | Lens | Title | File | Line | Rule |
|---|---|---|---|---|---|
| major | structural | Unnecessary abstraction wrapper | src/service.py | 42 | over-engineering |
| info | production_validation | New route registered but no caller found | src/api.py | 17 | declared-not-wired |

**Not flagged by this review:**
- structural: Does not validate business correctness or external runtime behavior.
- production_validation: Does not exercise live deployments or runtime infrastructure state.
```

**Fields explained:**

- **verdict**: `pass`, `block`, or `degraded` (partial lens coverage)
- **lenses line**: declared lenses for this risk level, how many reported results, how many were unavailable
- **findings table**: each row is one finding with lens attribution, severity, file path, line, and rule ID where applicable
- **not-flagged section**: explicit scope-honesty statements per lens — what each lens deliberately does not check

When verdict is `block`, the PR check status also turns red, gating merge until findings are resolved or waived via the admin API.

---

## 5. Your first MCP pre-commit call *(local — no Azure required for the call itself)*

The MCP server accepts JSON-RPC 2.0 over HTTP POST at `/mcp`. You need a valid Entra Bearer token scoped to the audience you configured.

### Get a token

```bash
# Using Azure CLI (assuming your identity has access)
TOKEN=$(az account get-access-token \
  --resource api://agentic-harness \
  --query accessToken -o tsv)
```

### Discover available tools

```bash
curl -s -X POST https://ca-<prefix>-mcpserver.<env>.azurecontainerapps.io/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {}
  }'
```

Response includes these tools:

| Tool name | What it does |
|---|---|
| `review.validate_change` | Pre-commit review of a working-tree diff |
| `get_active_findings` | Active findings (candidate/confirmed/reopened) for a repo |
| `get_risk_explanation` | Risk level and signals for a change ID |
| `get_related_changes` | Other changes sharing dedup keys or overlapping files |

### Submit a diff for pre-commit review

```bash
curl -s -X POST https://ca-<prefix>-mcpserver.<env>.azurecontainerapps.io/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "review.validate_change",
      "arguments": {
        "repo_id": "org/your-repo",
        "diff": "diff --git a/src/auth.py b/src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1,3 +1,5 @@\n+def check_admin(user):\n+    return True\n"
      }
    }
  }'
```

Response shape (sync, diffs up to 500 lines):

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{
      "type": "text",
      "text": "{\"mode\": \"sync\", \"findingCount\": 1, \"blocking\": 1, \"findings\": [{\"lens\": \"security\", \"severity\": \"blocker\", \"title\": \"Authorization always returns true\", \"path\": \"src/auth.py\", \"line\": 2}]}"
    }]
  }
}
```

For diffs longer than 500 lines, `mode` returns `"async"` and you submit via pull request instead.

### Query active findings

```bash
curl -s -X POST https://ca-<prefix>-mcpserver.<env>.azurecontainerapps.io/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "get_active_findings",
      "arguments": {"repo_id": "org/your-repo"}
    }
  }'
```

---

## 6. Add custom review rules *(optional, no Azure required)*

Drop Markdown files into `.harness/rules/*.md` in any repository the harness reviews. The worker discovers and applies them automatically.

Each file requires YAML frontmatter followed by a plain-text instruction body:

```markdown
---
id: no-sync-db-calls
severity: major
applies_to: ["src/db/**", "!src/db/migrations/**"]
lens: security
---
Never introduce synchronous database calls in request handlers.
Use the async ORM session exclusively; blocking I/O in async handlers causes request starvation.
```

**Frontmatter fields:**

| Field | Required | Values |
|---|---|---|
| `id` | yes | unique string within the repo |
| `severity` | yes | `blocker`, `major`, `high`, `medium`, `minor`, `low`, `info` |
| `applies_to` | yes | glob list; prefix `!` to exclude |
| `lens` | yes | `structural`, `security`, `correctness`, `architecture`, `test_quality`, `production_validation`, or `general` |

The instruction body becomes the lens prompt injection. It must be non-empty. Duplicate `id` values cause both rules to be skipped with an error surfaced in the run record.

---

## Local development

No Azure account needed for unit and contract tests:

```powershell
uv sync --extra dev --extra api --extra mcp
uv run pytest --tb=short          # unit + contract suites
uv run ruff check .
```

The benchmark baseline requires a live Foundry endpoint:

```powershell
az login
uv run python scripts/run_baseline.py --gate
```
