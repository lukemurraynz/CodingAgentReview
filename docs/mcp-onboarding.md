# MCP Onboarding: Connecting Your Coding Agent to the Engineering Harness

The harness exposes a Model Context Protocol (MCP) server at `/mcp`. Any MCP-capable coding agent — GitHub Copilot CLI, Claude Code, Cursor, or any custom client — can connect with zero local installation beyond authentication.

---

## What the MCP server provides

| Tool | Description |
|---|---|
| `review.validate_change` | Submit a working-tree diff for pre-commit review; returns structured findings synchronously for diffs up to 500 lines |
| `get_active_findings` | List active findings (candidate/confirmed/reopened) for a repository |
| `get_risk_explanation` | Risk level and contributing signals for a specific change ID |
| `get_related_changes` | Other changes sharing dedup keys or overlapping files with a given change or finding |
| `fix.propose` | Request a bounded patch proposal for given findings + diff; returns a proposal only - it never applies or commits anything |

Server name (as returned by `initialize`): `engineering-harness`

---

## Authentication

The MCP server validates Entra Bearer JWTs on every request. Requests without a valid token return HTTP 401 with JSON-RPC error code `-32001`.

**What you need from your platform team:**

| Item | Example value |
|---|---|
| MCP server URL | `https://ca-<prefix>-mcpserver.<env>.azurecontainerapps.io/mcp` |
| Entra tenant ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| Token audience | `api://agentic-harness` |

Your token must include `aud` matching the configured audience and `exp`/`nbf`/`iss` claims. The server enforces RS256 signature validation against the Entra JWKS endpoint.

To get a token using Azure CLI (your identity must have been granted access):

```bash
TOKEN=$(az account get-access-token \
  --resource api://agentic-harness \
  --query accessToken -o tsv)
```

---

## Client configuration by agent

Use `scripts/gen_mcp_config.py` to generate a ready-to-paste config block:

```bash
uv run python scripts/gen_mcp_config.py \
  --url https://ca-myprefix-mcpserver.myenv.azurecontainerapps.io/mcp \
  --tenant xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  --audience api://agentic-harness
```

The script prints a JSON block for each supported client format. Paste examples follow.

### GitHub Copilot CLI (`~/.config/gh/copilot/config.json`)

```json
{
  "mcpServers": {
    "engineering-harness": {
      "url": "https://ca-myprefix-mcpserver.myenv.azurecontainerapps.io/mcp",
      "headers": {
        "Authorization": "Bearer ${HARNESS_MCP_TOKEN}"
      }
    }
  }
}
```

Set `HARNESS_MCP_TOKEN` in your shell before starting the agent:

```bash
export HARNESS_MCP_TOKEN=$(az account get-access-token \
  --resource api://agentic-harness \
  --query accessToken -o tsv)
```

### Claude Code (`~/.claude.json` or workspace `.claude.json`)

```json
{
  "mcpServers": {
    "engineering-harness": {
      "type": "http",
      "url": "https://ca-myprefix-mcpserver.myenv.azurecontainerapps.io/mcp",
      "headers": {
        "Authorization": "Bearer ${HARNESS_MCP_TOKEN}"
      }
    }
  }
}
```

### Cursor (`.cursor/mcp.json` in repo root or `~/.cursor/mcp.json` for global)

```json
{
  "mcpServers": {
    "engineering-harness": {
      "url": "https://ca-myprefix-mcpserver.myenv.azurecontainerapps.io/mcp",
      "headers": {
        "Authorization": "Bearer ${HARNESS_MCP_TOKEN}"
      }
    }
  }
}
```

### Generic JSON-RPC over HTTP (any client)

POST to the MCP URL with `Content-Type: application/json` and `Authorization: Bearer <token>`. The protocol version is `2025-03-26`.

```bash
# Discover tools
curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HARNESS_MCP_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

---

## Token refresh

Entra tokens expire (typically after 1 hour). Refresh before they expire:

```bash
# Re-export before starting a session
export HARNESS_MCP_TOKEN=$(az account get-access-token \
  --resource api://agentic-harness \
  --query accessToken -o tsv)
```

For CI or service-account use, acquire tokens programmatically via the Entra client-credentials flow using a service principal your platform team provisions.

---

## Repository scoping

Each tool call is scoped to a specific repository. Your token's `repos` claim must include the `repo_id` you pass (e.g., `org/your-repo`). Requests for repositories outside the token scope return HTTP 403 with JSON-RPC error code `-32003`.

The `repo_id` format matches the owner/repo slug from your Git provider: `org/repo-name` for GitHub, `project/repo` for Azure DevOps.

---

## Verifying your connection

```bash
# Initialize session
curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HARNESS_MCP_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}'
```

Expected response includes `"name": "engineering-harness"` in `result.serverInfo`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| HTTP 401, error code `-32001` | Missing or expired token | Re-acquire token; check `HARNESS_MCP_TOKEN` is set |
| HTTP 401, `"MCP auth not configured"` | Server missing `HARNESS_MCP_ENTRA_TENANT_ID`/`HARNESS_MCP_ENTRA_AUDIENCE` | Contact platform team to verify env var configuration |
| HTTP 403, error code `-32003` | Token not scoped to this repo | Ask platform team to add `org/repo` to your token's `repos` claim |
| `mode: async` in `review.validate_change` response | Diff exceeds 500 lines | Submit via pull request for full review; the sync path only handles ≤500 changed lines |
| JSON-RPC error code `-32601` | Unknown method name | Check tool name spelling — use `tools/list` to enumerate available names |

## Granting an agent (service principal) access to a repository

Repo access comes from Entra **app roles** whose value is the repo id, plus a matching read scope:

1. On the `harness-mcp` app registration, add an application role with value `repo:org/repo:read`.
2. Assign that role to your agent's service principal (this constitutes admin consent).
3. Tokens minted via client-credentials will carry `roles: ["repo:org/repo:read"]` and are authorized for `org/repo` only.

Alternatively, tokens may carry a custom `"repos": ["org/repo"]` claim from your own claims provider - both shapes are honored.

