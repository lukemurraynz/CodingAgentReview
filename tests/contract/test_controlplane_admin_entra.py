"""Controlplane admin Entra auth contract tests (FR-019 / T016)."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from controlplane import create_app
from harness.models import Evidence, Finding, FindingCategory, Severity

TEST_TENANT = "11111111-2222-3333-4444-555555555555"
TEST_AUDIENCE = "api://agentic-harness-admin"
TEST_CLIENT_ID = "99999999-8888-7777-6666-555555555555"
TEST_ISSUER = f"https://login.microsoftonline.com/{TEST_TENANT}/v2.0"
REPO_ID = "org/repo"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class SigningMaterial:
    private_key: rsa.RSAPrivateKey
    public_jwk: dict[str, str]


class FakeJwksFetcher:
    def __init__(self, jwks: dict[str, object]) -> None:
        self._jwks = jwks

    async def fetch(self, jwks_url: str) -> dict[str, object]:
        return self._jwks


class InMemoryAdminRepository:
    def __init__(self, findings: list[Finding] | None = None) -> None:
        self._items = {(finding.repo_id, finding.id): finding for finding in findings or []}

    async def get_finding(self, repo_id: str, finding_id: str) -> Finding | None:
        return self._items.get((repo_id, finding_id))

    async def get_findings(self, repo_id: str) -> list[Finding]:
        return [finding for (stored_repo, _), finding in self._items.items() if stored_repo == repo_id]

    async def put_finding(self, finding: Finding) -> None:
        self._items[(finding.repo_id, finding.id)] = finding


def _finding(*, finding_id: str) -> Finding:
    return Finding(
        id=finding_id,
        change_id="change-1",
        repo_id=REPO_ID,
        category=FindingCategory.SECURITY,
        severity=Severity.HIGH,
        title="missing auth test",
        evidence=[Evidence(path="src/app.py", line_start=7)],
        dedup_key=f"dedup:{finding_id}",
        status="confirmed",
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signing_material() -> SigningMaterial:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    return SigningMaterial(
        private_key=private_key,
        public_jwk={
            "kty": "RSA",
            "use": "sig",
            "kid": "test-kid",
            "alg": "RS256",
            "n": _b64url_uint(public_numbers.n),
            "e": _b64url_uint(public_numbers.e),
        },
    )


def _token(
    signing_material: SigningMaterial,
    *,
    audience: str = TEST_AUDIENCE,
    issuer: str = TEST_ISSUER,
    expires_in: int = 300,
    repos: list[str] | None = None,
    scopes: str = "repo:org/repo:read repo:org/repo:write",
) -> str:
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "exp": now + expires_in,
        "nbf": now - 60,
        "iat": now,
        "oid": "principal-1",
        "scp": scopes,
        "repos": repos or [REPO_ID],
    }
    return jwt.encode(payload, signing_material.private_key, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture()
def signing_material() -> SigningMaterial:
    return _signing_material()


@pytest.fixture()
def entra_client_factory(monkeypatch, signing_material: SigningMaterial):
    from harness.authn import EntraTokenVerifier

    class TestEntraTokenVerifier(EntraTokenVerifier):
        def __init__(self, config) -> None:
            super().__init__(config, jwks_fetcher=FakeJwksFetcher({"keys": [signing_material.public_jwk]}))

    monkeypatch.delenv("HARNESS_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("HARNESS_ENTRA_TENANT_ID", TEST_TENANT)
    monkeypatch.setenv("HARNESS_ENTRA_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("HARNESS_ENTRA_CLIENT_ID", TEST_CLIENT_ID)
    monkeypatch.setattr("controlplane.admin.EntraTokenVerifier", TestEntraTokenVerifier)

    def _make(repository: InMemoryAdminRepository) -> TestClient:
        return TestClient(create_app(repository=repository))

    return _make


def test_admin_auth_accepts_valid_entra_token(entra_client_factory, signing_material: SigningMaterial) -> None:
    client = entra_client_factory(InMemoryAdminRepository([_finding(finding_id="f-entra")]))

    response = client.get(f"/admin/repos/{REPO_ID}/waivers", headers=_headers(_token(signing_material)))

    assert response.status_code == 200
    assert response.json() == []


def test_admin_auth_rejects_invalid_entra_token(entra_client_factory, signing_material: SigningMaterial) -> None:
    client = entra_client_factory(InMemoryAdminRepository([_finding(finding_id="f-entra-invalid")]))

    response = client.get(
        f"/admin/repos/{REPO_ID}/waivers",
        headers=_headers(_token(signing_material, audience="wrong-audience")),
    )

    assert response.status_code == 403
    assert response.json()["detail"].startswith("invalid bearer token:")


def test_admin_auth_accepts_legacy_secret_with_entra_config(entra_client_factory, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_ADMIN_TOKEN", "shared-secret")
    client = entra_client_factory(InMemoryAdminRepository([_finding(finding_id="f-secret")]))

    response = client.get(f"/admin/repos/{REPO_ID}/waivers", headers=_headers("shared-secret"))

    assert response.status_code == 200
    assert response.json() == []
