"""Control plane host: webhook intake + admin API (FR-001, FR-017)."""

from __future__ import annotations

import json
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from harness.authz import RepoAccessDenied
from harness.events import CanonicalEvent, EventType
from harness.models import ChangeType
from harness.queue import ReviewQueuePublisher
from providers.azuredevops import AzureDevOpsAdapter
from providers.github import GitHubAdapter

ADAPTERS = {
    GitHubAdapter.name: GitHubAdapter(),
    AzureDevOpsAdapter.name: AzureDevOpsAdapter(),
}

_publisher = ReviewQueuePublisher()


def _event_type_for(change_type: ChangeType) -> EventType:
    return EventType.PULL_REQUEST_CHANGED if change_type == ChangeType.PULL_REQUEST else EventType.COMMIT_CREATED



from harness.telemetry import configure_telemetry


def create_app() -> FastAPI:
    configure_telemetry()
    app = FastAPI(title="Agentic Engineering Harness — Control Plane", version="0.1.0")

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

        event = CanonicalEvent(
            id=str(uuid.uuid4()),
            event_type=_event_type_for(change.change_type),
            source=f"controlplane.webhooks.{provider}",
            subject=f"repos/{change.repo_id}",
            data={
                "changeId": change.id,
                "headSha": change.head_sha,
                "baseSha": change.base_sha,
                "repoId": change.repo_id,
                "provider": change.provider.value,
                "prNumber": change.pr_number,
                "classification": change.classification.value,
            },
        )
        try:
            # Durable review requires awaited delivery (FR-017): failures surface
            # as 503 so the webhook sender retries.
            await _publisher.enqueue(event.model_dump(mode="json"))
        except RuntimeError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": f"review queue unavailable: {exc}"},
                headers={"Retry-After": "30"},
            )
        return JSONResponse(
            status_code=202,
            content={"accepted": True, "changeId": change.id, "eventId": event.id},
        )

    @app.get("/admin/specifications")
    async def specifications() -> list[dict[str, object]]:
        """Specification listing endpoint shape; store wiring lands with US6 graph work."""
        return []

    return app
