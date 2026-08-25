"""Admin waiver routes for the control plane (FR-016 / T058)."""

from __future__ import annotations

import hmac
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from harness.authn import EntraAuthError, EntraTokenConfig, EntraTokenVerifier
from harness.authz import RepoAccessDenied, require_repo_access
from harness.lifecycle import LifecycleError, apply
from harness.models import Finding, Waiver

_ADMIN_TOKEN_ENV = "HARNESS_ADMIN_TOKEN"


class FindingAdminStore(Protocol):
    async def get_finding(self, repo_id: str, finding_id: str) -> Finding | None: ...

    async def get_findings(self, repo_id: str) -> list[Finding]: ...

    async def put_finding(self, finding: Finding) -> None: ...


class WaiverCreateRequest(BaseModel):
    """Request body for writing the immutable waiver record."""

    approver: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class WaiverResponse(BaseModel):
    """Serialized waiver plus the finding it belongs to."""

    finding_id: str = Field(alias="findingId")
    repo_id: str = Field(alias="repoId")
    status: str
    approver: str
    rationale: str
    decided_at: datetime = Field(alias="decidedAt")


def _admin_token() -> str:
    token = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"admin auth not configured; set {_ADMIN_TOKEN_ENV}",
        )
    return token


def _optional_admin_token() -> str | None:
    token = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    return token or None


async def _authorize(request: Request, *, repo_id: str, write: bool = False) -> None:
    entra_config = EntraTokenConfig.from_env()
    expected = _optional_admin_token()
    if entra_config is None and expected is None:
        _admin_token()
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")

    if expected and hmac.compare_digest(token, expected):
        return

    if entra_config is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid admin token")

    try:
        principal = await EntraTokenVerifier(entra_config).verify_principal(token)
        require_repo_access(principal, repo_id, write=write)
    except EntraAuthError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RepoAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _waiver_response(finding: Finding) -> WaiverResponse:
    waiver = finding.waiver
    if waiver is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="waiver not found")
    return WaiverResponse(
        findingId=finding.id,
        repoId=finding.repo_id,
        status=finding.status,
        approver=waiver.approver,
        rationale=waiver.rationale,
        decidedAt=waiver.decided_at,
    )


def _waived_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(
        (finding for finding in findings if finding.waiver is not None),
        key=lambda finding: finding.waiver.decided_at if finding.waiver is not None else finding.updated_at,
        reverse=True,
    )


def create_admin_router(repository: FindingAdminStore) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.post(
        "/repos/{repo_id:path}/findings/{finding_id}/waiver",
        response_model=WaiverResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_waiver(
        repo_id: str,
        finding_id: str,
        payload: WaiverCreateRequest,
        request: Request,
    ) -> WaiverResponse:
        await _authorize(request, repo_id=repo_id, write=True)
        finding = await repository.get_finding(repo_id, finding_id)
        if finding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
        if finding.waiver is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="waiver already exists for finding")

        try:
            waived = apply(
                finding,
                "waived",
                waiver=Waiver(approver=payload.approver, rationale=payload.rationale),
            )
        except LifecycleError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        await repository.put_finding(waived)
        return _waiver_response(waived)

    @router.get("/repos/{repo_id:path}/waivers", response_model=list[WaiverResponse])
    async def list_waivers(repo_id: str, request: Request) -> list[WaiverResponse]:
        await _authorize(request, repo_id=repo_id)
        findings = await repository.get_findings(repo_id)
        return [_waiver_response(finding) for finding in _waived_findings(findings)]

    @router.get("/repos/{repo_id:path}/waivers/{finding_id}", response_model=WaiverResponse)
    async def get_waiver(repo_id: str, finding_id: str, request: Request) -> WaiverResponse:
        await _authorize(request, repo_id=repo_id)
        finding = await repository.get_finding(repo_id, finding_id)
        if finding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
        return _waiver_response(finding)

    return router
