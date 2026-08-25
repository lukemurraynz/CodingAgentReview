"""Generate ready-to-paste MCP client config JSON for the engineering harness.

Usage:
    uv run python scripts/gen_mcp_config.py \\
        --url https://ca-myprefix-mcpserver.myenv.azurecontainerapps.io/mcp \\
        --tenant xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \\
        --audience api://agentic-harness

Outputs config blocks for: GitHub Copilot CLI, Claude Code, Cursor, and a
generic HTTP client snippet.  The token value is left as the environment
variable reference ${HARNESS_MCP_TOKEN} so no live credential is embedded.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Config:
    url: str
    tenant: str
    audience: str


def _copilot_cli(cfg: Config) -> dict[str, object]:
    return {
        "mcpServers": {
            "engineering-harness": {
                "url": cfg.url,
                "headers": {"Authorization": "Bearer ${HARNESS_MCP_TOKEN}"},
            }
        }
    }


def _claude_code(cfg: Config) -> dict[str, object]:
    return {
        "mcpServers": {
            "engineering-harness": {
                "type": "http",
                "url": cfg.url,
                "headers": {"Authorization": "Bearer ${HARNESS_MCP_TOKEN}"},
            }
        }
    }


def _cursor(cfg: Config) -> dict[str, object]:
    return {
        "mcpServers": {
            "engineering-harness": {
                "url": cfg.url,
                "headers": {"Authorization": "Bearer ${HARNESS_MCP_TOKEN}"},
            }
        }
    }


def _token_hint(cfg: Config) -> str:
    return (
        "# Acquire a token before starting your agent:\n"
        f"#   export HARNESS_MCP_TOKEN=$(az account get-access-token \\\n"
        f"#     --resource {cfg.audience} \\\n"
        f"#     --tenant {cfg.tenant} \\\n"
        "#     --query accessToken -o tsv)"
    )


def _dump(label: str, path: str, data: dict[str, object]) -> str:
    body = json.dumps(data, indent=2)
    return f"# {label} ({path})\n{body}\n"


def generate(cfg: Config) -> str:
    sections = [
        _token_hint(cfg),
        "",
        _dump("GitHub Copilot CLI", "~/.config/gh/copilot/config.json", _copilot_cli(cfg)),
        _dump("Claude Code", "~/.claude.json  or  <repo>/.claude.json", _claude_code(cfg)),
        _dump("Cursor", "~/.cursor/mcp.json  or  <repo>/.cursor/mcp.json", _cursor(cfg)),
    ]
    return "\n".join(sections)


def _parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="MCP server URL, e.g. https://ca-prefix-mcpserver.env.azurecontainerapps.io/mcp",
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="Entra tenant GUID (HARNESS_MCP_ENTRA_TENANT_ID value)",
    )
    parser.add_argument(
        "--audience",
        required=True,
        help="Token audience (HARNESS_MCP_ENTRA_AUDIENCE value, e.g. api://agentic-harness)",
    )
    args = parser.parse_args(argv)
    return Config(url=args.url, tenant=args.tenant, audience=args.audience)


def main(argv: list[str] | None = None) -> int:
    cfg = _parse_args(argv)
    print(generate(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
