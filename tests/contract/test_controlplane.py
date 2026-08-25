"""Controlplane webhook route contract tests (FR-001, FR-017)."""

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable

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


def _fake_enqueue_counter() -> tuple[dict[str, int], Callable[[object], Awaitable[None]]]:
    state = {"count": 0}

    async def _enqueue(payload: object) -> None:
        del payload
        state["count"] += 1

    return state, _enqueue


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


def test_duplicate_webhook_is_deduplicated(client, monkeypatch):
    state, enqueue = _fake_enqueue_counter()

    monkeypatch.setenv("HARNESS_SERVICEBUS_NS", "fake")
    import controlplane as cp

    cp._dedup_registry.clear()
    monkeypatch.setattr(cp, "_send_event", enqueue)
    monkeypatch.setattr(cp, "_events", cp.EventEmitter(cp._send_event))

    body = _pr_body()
    first = client.post("/webhooks/github", content=body, headers=_signed(body))
    second = client.post("/webhooks/github", content=body, headers=_signed(body))

    assert first.status_code == 202
    assert first.json()["deduplicated"] is False
    assert second.status_code == 202
    assert second.json() == {"accepted": True, "changeId": "org/repo#7@h1", "deduplicated": True}
    assert state["count"] == 2


def test_different_sha_is_not_deduplicated(client, monkeypatch):
    state, enqueue = _fake_enqueue_counter()

    monkeypatch.setenv("HARNESS_SERVICEBUS_NS", "fake")
    import controlplane as cp

    cp._dedup_registry.clear()
    monkeypatch.setattr(cp, "_send_event", enqueue)
    monkeypatch.setattr(cp, "_events", cp.EventEmitter(cp._send_event))

    body_one = _pr_body()
    body_two = json.dumps(
        {
            "action": "opened",
            "number": 7,
            "pull_request": {"number": 7, "head": {"sha": "h2"}, "base": {"sha": "b1"}},
            "repository": {"full_name": "org/repo"},
        }
    ).encode()

    first = client.post("/webhooks/github", content=body_one, headers=_signed(body_one))
    second = client.post("/webhooks/github", content=body_two, headers=_signed(body_two))

    assert first.status_code == 202 and first.json()["deduplicated"] is False
    assert second.status_code == 202 and second.json()["deduplicated"] is False
    assert state["count"] == 4


def test_dedup_ttl_expiry_allows_reenqueue(client, monkeypatch):
    state, enqueue = _fake_enqueue_counter()

    monkeypatch.setenv("HARNESS_SERVICEBUS_NS", "fake")
    monkeypatch.setenv("HARNESS_DEDUP_TTL_SECONDS", "10")
    import controlplane as cp

    cp._dedup_registry.clear()
    monkeypatch.setattr(cp, "_send_event", enqueue)
    monkeypatch.setattr(cp, "_events", cp.EventEmitter(cp._send_event))

    ticks = iter([100.0, 105.0, 111.0])
    monkeypatch.setattr(cp, "_monotonic", lambda: next(ticks))

    body = _pr_body()
    first = client.post("/webhooks/github", content=body, headers=_signed(body))
    second = client.post("/webhooks/github", content=body, headers=_signed(body))
    third = client.post("/webhooks/github", content=body, headers=_signed(body))

    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert third.json()["deduplicated"] is False
    assert state["count"] == 4


def test_admin_specifications_shape(client, tmp_path):
    r = client.get("/admin/specifications", params={"root": str(tmp_path)})
    assert r.status_code == 200 and r.json() == []

    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "specifications.json").write_text(
        '{"specifications": [{"id": "api-contract", "title": "API contract", "applies_to": ["api"]}]}',
        encoding="utf-8",
    )
    r = client.get("/admin/specifications", params={"root": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert "specifications.json" in body[0]["sourcePath"]
    assert body[0]["errors"] == []
    assert body[0]["specification"]["id"] == "api-contract"

    (tmp_path / "specs" / "specifications.json").write_text("not json", encoding="utf-8")
    r = client.get("/admin/specifications", params={"root": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()[0]["specification"] is None
    assert "invalid JSON" in r.json()[0]["errors"][0]["message"]
