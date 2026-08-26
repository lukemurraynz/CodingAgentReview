"""MCP server contract tests for auth, discovery, and state tools."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from mcpserver import create_mcp_app
from mcpserver.auth import AuthConfig, BearerTokenAuthenticator, JwksFetcher
from mcpserver.tools_state import StateQueryService

TEST_TENANT = "11111111-2222-3333-4444-555555555555"
TEST_AUDIENCE = "api://agentic-harness"
TEST_CLIENT_ID = "99999999-8888-7777-6666-555555555555"
TEST_ISSUER = f"https://login.microsoftonline.com/{TEST_TENANT}/v2.0"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class SigningMaterial:
    private_key: rsa.RSAPrivateKey
    public_jwk: dict[str, str]


def _signing_material() -> SigningMaterial:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "kid": "test-kid",
        "alg": "RS256",
        "n": _b64url_uint(public_numbers.n),
        "e": _b64url_uint(public_numbers.e),
    }
    return SigningMaterial(private_key=private_key, public_jwk=jwk)


class FakeJwksFetcher(JwksFetcher):
    def __init__(self, jwks: dict[str, object]) -> None:
        self._jwks = jwks

    async def fetch(self, jwks_url: str) -> dict[str, object]:
        return self._jwks


class FakeStateQueryService(StateQueryService):
    def __init__(self) -> None:
        super().__init__(repository=None)
        self._active_findings: list[dict[str, object]] = [
            {
                "id": "f-1",
                "changeId": "chg-1",
                "category": "security",
                "severity": "high",
                "title": "Authorization bypass",
                "status": "confirmed",
            }
        ]
        self._risk: dict[str, object] = {
            "changeId": "chg-1",
            "repoId": "org/repo",
            "risk": "high",
            "reasons": ["security_boundary_changed: high"],
        }
        self._related: dict[str, list[dict[str, object]]] = {
            "chg-empty": [],
            "chg-1": [
                {
                    "changeId": "chg-2",
                    "repoId": "org/repo",
                    "findingIds": ["f-2"],
                    "sharedDedupeKeys": ["dup-1"],
                    "overlappingFiles": ["src/app.py"],
                }
            ],
            "finding:f-1": [
                {
                    "changeId": "chg-2",
                    "repoId": "org/repo",
                    "findingIds": ["f-2"],
                    "sharedDedupeKeys": ["dup-1"],
                    "overlappingFiles": ["src/app.py"],
                }
            ],
        }

    async def get_active_findings(self, repo_id: str) -> list[dict[str, object]]:
        assert repo_id == "org/repo"
        return self._active_findings

    async def get_risk_explanation(self, change_id: str) -> dict[str, object]:
        assert change_id == "chg-1"
        return self._risk

    async def get_related_changes(
        self,
        *,
        change_id: str | None = None,
        finding_id: str | None = None,
    ) -> list[dict[str, object]]:
        if change_id:
            return self._related.get(change_id, [])
        return self._related.get(f"finding:{finding_id}", [])

    async def repo_id_for_change(self, change_id: str) -> str | None:
        if change_id in {"chg-1", "chg-empty"}:
            return "org/repo"
        if change_id == "chg-foreign":
            return "other/repo"
        return None

    async def repo_id_for_finding(self, finding_id: str) -> str | None:
        if finding_id == "f-1":
            return "org/repo"
        if finding_id == "f-foreign":
            return "other/repo"
        return None


async def _fake_review_tool(*, repo_id: str, diff: str) -> dict[str, object]:
    return {"mode": "sync", "findingCount": 1, "blocking": 0, "findings": [{"repoId": repo_id, "diff": diff}]}


@pytest.fixture()
def signing_material() -> SigningMaterial:
    return _signing_material()


@pytest.fixture()
def auth_config() -> AuthConfig:
    return AuthConfig(
        tenant_id=TEST_TENANT,
        audience=TEST_AUDIENCE,
        client_id=TEST_CLIENT_ID,
        issuer=TEST_ISSUER,
        jwks_url=f"https://login.microsoftonline.com/{TEST_TENANT}/discovery/v2.0/keys",
    )


@pytest.fixture()
def state_service() -> FakeStateQueryService:
    return FakeStateQueryService()


@pytest.fixture()
def client(
    signing_material: SigningMaterial,
    auth_config: AuthConfig,
    state_service: FakeStateQueryService,
) -> TestClient:
    authenticator = BearerTokenAuthenticator(
        config=auth_config,
        jwks_fetcher=FakeJwksFetcher({"keys": [signing_material.public_jwk]}),
    )
    return TestClient(
        create_mcp_app(authenticator=authenticator, state_service=state_service, review_tool=_fake_review_tool)
    )


def _token(
    signing_material: SigningMaterial,
    *,
    audience: str = TEST_AUDIENCE,
    issuer: str = TEST_ISSUER,
    expires_in: int = 300,
    not_before_offset: int = -60,
    repos: list[str] | None = None,
    scopes: str = "repo:org/repo:read repo:all:read",
) -> str:
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "exp": now + expires_in,
        "nbf": now + not_before_offset,
        "iat": now,
        "oid": "principal-1",
        "scp": scopes,
        "repos": repos or ["org/repo"],
    }
    return jwt.encode(payload, signing_material.private_key, algorithm="RS256", headers={"kid": "test-kid"})


def _rpc(method: str, params: dict[str, object] | None = None, msg_id: int = 1) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_tools_list_discovery(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post("/mcp", json=_rpc("tools/list"), headers=_auth_headers(token))
    assert response.status_code == 200
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert names == [
        "review.validate_change",
        "fix.propose",
        "get_active_findings",
        "get_risk_explanation",
        "get_related_changes",
    ]


def test_initialize_happy_path(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc("initialize", {"protocolVersion": "2025-03-26"}),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "engineering-harness"


def test_review_validate_change_happy_path(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "review.validate_change", "arguments": {"repo_id": "org/repo", "diff": "diff --git a b"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    text = response.json()["result"]["content"][0]["text"]
    assert '"findingCount": 1' in text
    assert '"repoId": "org/repo"' in text


def test_review_validate_change_independent_of_event_queue(
    client: TestClient,
    signing_material: SigningMaterial,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T044: the interactive lane executes inline and must never route through the event queue."""
    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("interactive lane touched the event queue")

    monkeypatch.setattr("harness.queue.ReviewQueuePublisher.enqueue", _boom, raising=False)
    monkeypatch.setattr("controlplane._publisher.enqueue", _boom, raising=False)
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "review.validate_change", "arguments": {"repo_id": "org/repo", "diff": "diff --git a b"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert '"findingCount": 1' in response.json()["result"]["content"][0]["text"]


def test_get_active_findings_happy_path(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_active_findings", "arguments": {"repo_id": "org/repo"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert '"items": [{"id": "f-1"' in response.json()["result"]["content"][0]["text"]


def test_get_risk_explanation_happy_path(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_risk_explanation", "arguments": {"change_id": "chg-1"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert '"risk": "high"' in response.json()["result"]["content"][0]["text"]


def test_get_related_changes_empty(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_related_changes", "arguments": {"change_id": "chg-empty"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["text"] == '{"items": []}'


def test_get_related_changes_populated_by_change_id(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_related_changes", "arguments": {"change_id": "chg-1"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    text = response.json()["result"]["content"][0]["text"]
    assert '"sharedDedupeKeys": ["dup-1"]' in text
    assert '"overlappingFiles": ["src/app.py"]' in text


def test_get_related_changes_populated_by_finding_id(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_related_changes", "arguments": {"finding_id": "f-1"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    assert '"changeId": "chg-2"' in response.json()["result"]["content"][0]["text"]


def test_malformed_json_body_returns_parse_error(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post("/mcp", content=b"{bad", headers=_auth_headers(token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


def test_unknown_method_returns_jsonrpc_error(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post("/mcp", json=_rpc("wat"), headers=_auth_headers(token))
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_missing_auth_returns_401(client: TestClient) -> None:
    response = client.post("/mcp", json=_rpc("tools/list"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32001


def test_invalid_token_returns_401(client: TestClient) -> None:
    response = client.post("/mcp", json=_rpc("tools/list"), headers=_auth_headers("bad.token"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32001


def test_expired_token_returns_401(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material, expires_in=-1)
    response = client.post("/mcp", json=_rpc("tools/list"), headers=_auth_headers(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32001


def test_valid_token_path_with_fake_jwks(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material)
    response = client.post("/mcp", json=_rpc("tools/list"), headers=_auth_headers(token))
    assert response.status_code == 200


def test_repo_scope_rejection_returns_403(client: TestClient, signing_material: SigningMaterial) -> None:
    token = _token(signing_material, repos=["org/repo"])
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_active_findings", "arguments": {"repo_id": "other/repo"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == -32003


def test_repo_scope_rejection_on_related_changes_returns_403(
    client: TestClient,
    signing_material: SigningMaterial,
) -> None:
    token = _token(signing_material)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_related_changes", "arguments": {"change_id": "chg-foreign"}},
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == -32003


def test_missing_auth_config_fails_closed(
    signing_material: SigningMaterial,
    state_service: FakeStateQueryService,
) -> None:
    app = create_mcp_app(
        authenticator=BearerTokenAuthenticator(
            config=None,
            jwks_fetcher=FakeJwksFetcher({"keys": [signing_material.public_jwk]}),
        ),
        state_service=state_service,
        review_tool=_fake_review_tool,
    )
    client = TestClient(app)
    response = client.post("/mcp", json=_rpc("tools/list"), headers=_auth_headers(_token(signing_material)))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32001
