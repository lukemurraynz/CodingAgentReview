"""MCP server — vendor-neutral review & engineering-state interface (FR-002/005)."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from harness.authz import RepoAccessDenied

from .auth import BearerTokenAuthenticator, MpcAuthError, require_repo
from .tools_fix import create_fix_propose_tool
from .tools_review import review_validate_change
from .tools_state import StateQueryService

ToolHandler = Callable[..., Awaitable[dict[str, object] | list[dict[str, object]]]]


def _error_response(*, msg_id: object, code: int, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}},
    )


def _content_response(msg_id: object, result: dict[str, object] | list[dict[str, object]]) -> JSONResponse:
    payload = {"items": result} if isinstance(result, list) else result
    return JSONResponse(
        {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
    )


def create_mcp_app(
    *,
    authenticator: BearerTokenAuthenticator | None = None,
    state_service: StateQueryService | None = None,
    review_tool: ToolHandler | None = None,
    fix_tool: ToolHandler | None = None,
) -> FastAPI:
    api = FastAPI(title="engineering-harness-mcp")
    auth = authenticator or BearerTokenAuthenticator()
    state = state_service or StateQueryService()
    review = review_tool or review_validate_change
    fix = fix_tool or create_fix_propose_tool()

    @api.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    tools: dict[str, tuple[ToolHandler, str, dict[str, object]]] = {
        "review.validate_change": (
            review,
            "Review an uncommitted working-tree diff and return structured findings.",
            {
                "type": "object",
                "properties": {"repo_id": {"type": "string"}, "diff": {"type": "string"}},
                "required": ["repo_id", "diff"],
            },
        ),
        "fix.propose": (
            fix,
            "Propose a bounded patch for the provided diff and findings.",
            {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "diff": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "detail": {"type": "string"},
                                "path": {"type": "string"},
                                "line": {"type": "integer"},
                            },
                            "required": ["title", "path"],
                        },
                    },
                    "ci_log": {"type": "string"},
                },
                "required": ["repo_id", "diff"],
            },
        ),
        "get_active_findings": (
            state.get_active_findings,
            "Active findings for a repository.",
            {"type": "object", "properties": {"repo_id": {"type": "string"}}, "required": ["repo_id"]},
        ),
        "get_risk_explanation": (
            state.get_risk_explanation,
            "Recorded risk drivers for a change.",
            {"type": "object", "properties": {"change_id": {"type": "string"}}, "required": ["change_id"]},
        ),
        "get_related_changes": (
            state.get_related_changes,
            "Related changes that share dedupe keys or overlapping files.",
            {
                "type": "object",
                "properties": {"change_id": {"type": "string"}, "finding_id": {"type": "string"}},
            },
        ),
    }

    @api.post("/mcp")
    async def mcp_endpoint(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return _error_response(msg_id=None, code=-32700, message="invalid json", status_code=400)
        msg_id = body.get("id")
        try:
            principal = await auth.authenticate(request)
        except MpcAuthError as exc:
            return _error_response(msg_id=msg_id, code=-32001, message=str(exc), status_code=401)
        method = body.get("method", "")
        params = body.get("params") or {}

        if method == "initialize":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "engineering-harness", "version": "0.1.0"},
                    },
                }
            )
        if method == "notifications/initialized":
            return JSONResponse(status_code=202, content={})
        if method == "tools/list":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {"name": name, "description": description, "inputSchema": schema}
                            for name, (_, description, schema) in tools.items()
                        ]
                    },
                }
            )
        if method != "tools/call":
            return _error_response(msg_id=msg_id, code=-32601, message=f"unknown method {method}")
        name = params.get("name")
        if name not in tools:
            return _error_response(msg_id=msg_id, code=-32601, message=f"unknown tool {name}")
        arguments = params.get("arguments") or {}
        try:
            await _enforce_repo_scope(principal=principal, state=state, tool_name=str(name), arguments=arguments)
            result = await tools[str(name)][0](**arguments)
        except RepoAccessDenied as exc:
            return _error_response(msg_id=msg_id, code=-32003, message=str(exc), status_code=403)
        except ValueError as exc:
            return _error_response(msg_id=msg_id, code=-32602, message=str(exc), status_code=400)
        return _content_response(msg_id, result)

    return api


async def _enforce_repo_scope(
    *, principal, state: StateQueryService, tool_name: str, arguments: dict[str, Any]
) -> None:
    if tool_name in {"review.validate_change", "fix.propose", "get_active_findings"}:
        repo_id = arguments.get("repo_id")
        if not isinstance(repo_id, str) or not repo_id:
            raise ValueError("repo_id is required")
        require_repo(principal, repo_id)
        return
    if tool_name == "get_risk_explanation":
        change_id = arguments.get("change_id")
        if not isinstance(change_id, str) or not change_id:
            raise ValueError("change_id is required")
        repo_id = await state.repo_id_for_change(change_id)
        if repo_id is None:
            return
        require_repo(principal, repo_id)
        return
    if tool_name == "get_related_changes":
        change_id = arguments.get("change_id")
        finding_id = arguments.get("finding_id")
        if bool(change_id) == bool(finding_id):
            raise ValueError("provide exactly one of change_id or finding_id")
        if change_id:
            repo_id = await state.repo_id_for_change(str(change_id))
        else:
            repo_id = await state.repo_id_for_finding(str(finding_id))
        if repo_id is None:
            return
        require_repo(principal, repo_id)


def main() -> None:
    from harness.telemetry import configure_telemetry

    configure_telemetry()
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run("mcpserver:create_mcp_app", factory=True, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
