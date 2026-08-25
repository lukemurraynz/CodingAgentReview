"""Controlplane webhook route contract tests (FR-001, FR-017)."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from controlplane import create_app

SECRET = "test-secret"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("HARNESS_GITHUB_WEBHOOK_SECRET", SECRET)
    return TestClient(create_app())


def _signed(body: bytes) -> dict[str, str]:
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"x-hub-signature-256": sig, "x-github-event": "pull_request", "Content-Type": "application/json"}


def _pr_body() -> bytes:
    return json.dumps(
        {
            "action": "opened",
            "number": 7,
            "pull_request": {
                "number": 7,
                "head": {"sha": "h1"},
                "base": {"sha": "b1"},
            },
            "repository": {"full_name": "org/repo"},
        }
    ).encode()


def test_healthz_no_azure_deps(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_unknown_provider_404(client):
    r = client.post("/webhooks/gitea", content=b"{}", headers={})
    assert r.status_code == 404


def test_bad_signature_401(client):
    r = client.post("/webhooks/github", content=_pr_body(), headers={"x-hub-signature-256": "sha256=bad"})
    assert r.status_code == 401


def test_irrelevant_event_204(client):
    body = json.dumps({"action": "labeled"}).encode()
    r = client.post("/webhooks/github", content=body, headers=_signed(body))
    assert r.status_code == 204


def test_queue_unconfigured_503_with_retry_after(client, monkeypatch):
    # No HARNESS_SERVICEBUS_NS → publisher raises RuntimeError → 503 + Retry-After
    monkeypatch.delenv("HARNESS_SERVICEBUS_NS", raising=False)
    r = client.post("/webhooks/github", content=_pr_body(), headers=_signed(_pr_body()))
    assert r.status_code == 503
    assert r.headers.get("retry-after") == "30"


def test_accepted_when_queue_configured(client, monkeypatch):
    class FakePublisher:
        def enqueue(self, payload):
            async def _send():
                return None
            return _send()

    monkeypatch.setenv("HARNESS_SERVICEBUS_NS", "fake")
    import controlplane as cp
    monkeypatch.setattr(cp, "_publisher", FakePublisher())
    r = client.post("/webhooks/github", content=_pr_body(), headers=_signed(_pr_body()))
    assert r.status_code == 202
    assert r.json()["accepted"] is True
    assert "changeId" in r.json() and "eventId" in r.json()


def test_admin_specifications_shape(client):
    r = client.get("/admin/specifications")
    assert r.status_code == 200 and r.json() == []
