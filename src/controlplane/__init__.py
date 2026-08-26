"""Control plane host: webhook intake + admin API (FR-001, FR-017)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from controlplane.admin import FindingAdminStore, create_admin_router
from graph import discover_specification_artifacts, load_specifications
from harness.authz import RepoAccessDenied
from harness.events import CommitCreatedData, EventEmitter, PullRequestChangedData, ReviewReceivedData
from harness.models import ChangeType
from harness.queue import ReviewQueuePublisher
from harness.repository import DeliveryDedupStore, HarnessRepository
from harness.telemetry import configure_telemetry
from providers.azuredevops import AzureDevOpsAdapter
from providers.github import GitHubAdapter

ADAPTERS = {
    GitHubAdapter.name: GitHubAdapter(),
    AzureDevOpsAdapter.name: AzureDevOpsAdapter(),
}

_publisher = ReviewQueuePublisher()
_DEDUP_TTL_SECONDS_DEFAULT = 600.0


async def _send_event(event):  # type: ignore[no-untyped-def]
    await _publisher.enqueue(event.model_dump(mode="json"))


_events = EventEmitter(_send_event)


def _dedup_ttl_seconds() -> float:
    raw = os.environ.get("HARNESS_DEDUP_TTL_SECONDS")
    if raw is None:
        return _DEDUP_TTL_SECONDS_DEFAULT
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return _DEDUP_TTL_SECONDS_DEFAULT


def _dedup_key(
    provider: str,
    change_id: str,
    repo_id: str,
    pr_number: int | None,
    head_sha: str,
) -> tuple[str, str, str]:
    del provider, change_id
    pr_or_commit = str(pr_number) if pr_number is not None else "commit"
    return repo_id, pr_or_commit, head_sha


def _as_delivery_dedup_store(candidate: object) -> DeliveryDedupStore | None:
    if isinstance(candidate, DeliveryDedupStore):
        return candidate
    return None


def create_app(
    *,
    repository: FindingAdminStore | None = None,
    delivery_store: DeliveryDedupStore | None = None,
    dedup_now: Callable[[], float] | None = None,
) -> FastAPI:
    configure_telemetry()
    admin_repository = repository or HarnessRepository()
    dedup_repository: DeliveryDedupStore | None = delivery_store or _as_delivery_dedup_store(admin_repository)
    app = FastAPI(title="Agentic Engineering Harness — Control Plane", version="0.1.0")
    app.include_router(create_admin_router(admin_repository))

    @app.exception_handler(PermissionError)
    async def _permission(request: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    @app.exception_handler(RepoAccessDenied)
    async def _denied(request: Request, exc: RepoAccessDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": str(exc)})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/{provider}")
    async def webhook(provider: str, request: Request, response: Response) -> Response:
        adapter = ADAPTERS.get(provider)
        if adapter is None:
            return JSONResponse(status_code=404, content={"error": f"unknown provider {provider}"})
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            change = adapter.parse_webhook(headers, body)
        except PermissionError as exc:
            return JSONResponse(status_code=401, content={"error": str(exc)})
        except json.JSONDecodeError as exc:
            return JSONResponse(status_code=400, content={"error": f"invalid payload: {exc}"})
        if change is None:
            response.status_code = 204
            return response

        if dedup_repository is None:
            return JSONResponse(
                status_code=503,
                content={"error": "delivery dedup store unavailable"},
                headers={"Retry-After": "30"},
            )

        dedup_key = _dedup_key(provider, change.id, change.repo_id, change.pr_number, change.head_sha)
        if not await dedup_repository.claim_delivery(
            dedup_key,
            ttl_seconds=_dedup_ttl_seconds(),
            now=None if dedup_now is None else dedup_now(),
        ):
            return JSONResponse(
                status_code=202,
                content={"accepted": True, "changeId": change.id, "deduplicated": True},
            )

        source = f"controlplane.webhooks.{provider}"
        subject = f"repos/{change.repo_id}"
        try:
            # Durable review requires awaited delivery (FR-017): failures surface
            # as 503 so the webhook sender retries.
            if change.change_type == ChangeType.PULL_REQUEST:
                event = await _events.pull_request_changed(
                    source=source,
                    subject=subject,
                    payload=PullRequestChangedData(
                        changeId=change.id,
                        headSha=change.head_sha,
                        baseSha=change.base_sha,
                        repoId=change.repo_id,
                        provider=change.provider.value,
                        prNumber=change.pr_number,
                        classification=change.classification.value,
                    ),
                )
            else:
                event = await _events.commit_created(
                    source=source,
                    subject=subject,
                    payload=CommitCreatedData(
                        changeId=change.id,
                        headSha=change.head_sha,
                        baseSha=change.base_sha,
                        repoId=change.repo_id,
                        provider=change.provider.value,
                        classification=change.classification.value,
                    ),
                )
            await _events.review_received(
                source=source,
                subject=subject,
                payload=ReviewReceivedData(
                    changeId=change.id,
                    repoId=change.repo_id,
                    headSha=change.head_sha,
                    provider=change.provider.value,
                ),
            )
        except RuntimeError as exc:
            await dedup_repository.release_delivery_claim(dedup_key)
            return JSONResponse(
                status_code=503,
                content={"error": f"review queue unavailable: {exc}"},
                headers={"Retry-After": "30"},
            )
        return JSONResponse(
            status_code=202,
            content={"accepted": True, "changeId": change.id, "eventId": event.id, "deduplicated": False},
        )

    @app.get("/admin/specifications")
    async def specifications(root: str = ".") -> list[dict[str, object]]:
        """Declared specifications discovered under `root` (FR-033); load errors returned as data."""
        artifacts = discover_specification_artifacts(root)
        return [
            {
                "sourcePath": loaded.source_path,
                "specification": loaded.specification.model_dump(by_alias=True) if loaded.specification else None,
                "errors": [{"path": error.path, "message": error.message} for error in loaded.errors],
            }
            for loaded in load_specifications(artifacts)
        ]

    return app
