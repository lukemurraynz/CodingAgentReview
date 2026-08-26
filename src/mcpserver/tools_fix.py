"""Pre-commit fix proposal tool implementation."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping, Sequence

from pydantic import BaseModel, Field

from fixer import FixProposal, FixValidator, ValidationResult, create_fix_session
from harness.credentials import ScopedAsyncCredential
from harness.models import Evidence, Finding, FindingCategory, Severity
from lenses.diffparse import parse_unified_diff

_DEFAULT_CHANGE_ID = "adhoc-fix"
_ENDPOINT_ENV = "HARNESS_FOUNDRY_ENDPOINT"


class ProposedFinding(BaseModel):
    """Caller-supplied finding anchor for fix generation."""

    title: str = Field(min_length=1)
    detail: str = ""
    path: str = Field(min_length=1)
    line: int | None = Field(default=None, ge=1)


class FixRequest(BaseModel):
    """Typed input for the fix.propose MCP tool."""

    repo_id: str = Field(min_length=1)
    diff: str = Field(min_length=1)
    findings: tuple[ProposedFinding, ...] = ()
    ci_log: str | None = None


class FoundryFixModelClient:
    """Adapter exposing the FixSession complete() contract through the Foundry review lane."""

    async def complete(self, *, system_prompt: str, user_prompt: str) -> dict[str, object]:
        endpoint = _endpoint()
        if endpoint is None:
            raise RuntimeError(f"{_ENDPOINT_ENV} not configured")
        from azure.ai.inference.aio import ChatCompletionsClient
        from azure.ai.inference.models import SystemMessage, UserMessage
        from azure.identity.aio import DefaultAzureCredential

        async with ScopedAsyncCredential(DefaultAzureCredential()) as credential:
            client = ChatCompletionsClient(endpoint=endpoint, credential=credential)
            try:
                response = await client.complete(
                    messages=[
                        SystemMessage(content=system_prompt),
                        UserMessage(content=user_prompt),
                    ],
                    model=_deployment(),
                    temperature=0.0,
                    max_tokens=1200,
                )
            finally:
                await client.close()
        usage = getattr(response, "usage", None)
        text = ""
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                text = content
        return {
            "text": text,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }


def _endpoint() -> str | None:
    return os.environ.get(_ENDPOINT_ENV)


FixToolHandler = Callable[..., Awaitable[dict[str, object]]]


def create_fix_propose_tool(
    *,
    model_client: FoundryFixModelClient | object | None = None,
    validators: Sequence[FixValidator] | None = None,
) -> FixToolHandler:
    """Create the MCP fix tool with injectable model and validation seams."""

    async def fix_propose(
        *,
        repo_id: str,
        diff: str,
        findings: list[dict[str, object]] | None = None,
        ci_log: str | None = None,
    ) -> dict[str, object]:
        request = FixRequest.model_validate(
            {
                "repo_id": repo_id,
                "diff": diff,
                "findings": findings or [],
                "ci_log": ci_log,
            }
        )
        session = create_fix_session(
            diff_text=request.diff,
            findings=_coerce_findings(request),
            model_client=model_client or FoundryFixModelClient(),
            validators=tuple(validators or (_default_validator(request),)),
            ci_failure_log_text=request.ci_log,
            max_iterations=_max_iterations_default(),
        )
        return _serialize_proposal(await session.run())

    return fix_propose


def _deployment() -> str:
    return os.environ.get("HARNESS_FOUNDRY_DEPLOYMENT", "model-router")


def _max_iterations_default() -> int:
    raw = os.environ.get("HARNESS_FIX_MAX_ITERATIONS")
    return int(raw) if raw is not None else 3


def _coerce_findings(request: FixRequest) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for index, finding in enumerate(request.findings, start=1):
        findings.append(
            Finding(
                id=f"fix-input-{index}",
                change_id=_DEFAULT_CHANGE_ID,
                repo_id=request.repo_id,
                category=FindingCategory.CORRECTNESS,
                severity=Severity.MEDIUM,
                title=finding.title,
                detail=finding.detail,
                evidence=[
                    Evidence(
                        path=finding.path,
                        line_start=finding.line,
                        line_end=finding.line,
                        rule_id="fix.propose",
                    )
                ],
                dedup_key=f"{finding.path}:{finding.line or 0}:{finding.title}"[:200],
            )
        )
    return tuple(findings)


def _default_validator(request: FixRequest) -> FixValidator:
    expected_paths = frozenset(finding.path for finding in request.findings)

    def _validate(diff_text: str, files: Mapping[str, str]) -> ValidationResult:
        parsed_files = parse_unified_diff(diff_text)
        if not parsed_files:
            return ValidationResult(False, "patch produced no changes")
        if not files:
            return ValidationResult(False, "patch produced no file content")
        if expected_paths and expected_paths.isdisjoint(parsed.path for parsed in parsed_files):
            return ValidationResult(False, "patch did not touch any finding path")
        return ValidationResult(True, "patch applied within allowed files")

    return _validate


def _serialize_proposal(proposal: FixProposal) -> dict[str, object]:
    return {
        "status": proposal.status,
        "iterations": len(proposal.iterations),
        "final_diff": proposal.final_diff,
        "notes": list(proposal.notes),
    }
