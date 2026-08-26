"""HTTP contract tests for the fix.propose MCP tool."""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from fixer import ValidationResult
from mcpserver import create_mcp_app
from mcpserver.auth import AuthConfig, BearerTokenAuthenticator, JwksFetcher
from mcpserver.tools_fix import create_fix_propose_tool

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


class FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        assert system_prompt
        assert user_prompt
        return self._responses.pop(0) if self._responses else ""


def _validator_outcomes(results: list[ValidationResult]):
    pending = list(results)

    def _validate(diff_text: str, files: Mapping[str, str]) -> ValidationResult:
        assert diff_text
        assert "src/app.py" in files
        return pending.pop(0)

    return _validate


def _always_valid(diff_text: str, files: Mapping[str, str]) -> ValidationResult:
    assert diff_text
    assert files
    return ValidationResult(True, "tests green")


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


def _client(
    *,
    signing_material: SigningMaterial,
    auth_config: AuthConfig,
    fix_tool,
) -> TestClient:
    authenticator = BearerTokenAuthenticator(
        config=auth_config,
        jwks_fetcher=FakeJwksFetcher({"keys": [signing_material.public_jwk]}),
    )
    return TestClient(create_mcp_app(authenticator=authenticator, fix_tool=fix_tool))


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


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _rpc(method: str, params: dict[str, object] | None = None, msg_id: int = 1) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


def _base_diff() -> str:
    return "\n".join(
        [
            "diff --git a/src/app.py b/src/app.py",
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1,2 +1,2 @@",
            " def run():",
            "-    return 'bad'",
            "+    return 'bad'",
        ]
    )


def _call_fix(client: TestClient, token: str) -> dict[str, object]:
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "fix.propose",
                "arguments": {
                    "repo_id": "org/repo",
                    "diff": _base_diff(),
                    "findings": [
                        {
                            "title": "Fix bug",
                            "detail": "return value should be corrected",
                            "path": "src/app.py",
                            "line": 2,
                        }
                    ],
                },
            },
        ),
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()


def _content_text(payload: dict[str, object]) -> str:
    result = payload.get("result")
    assert isinstance(result, dict)
    content = result.get("content")
    assert isinstance(content, list)
    assert content
    first = content[0]
    assert isinstance(first, dict)
    text = first.get("text")
    assert isinstance(text, str)
    return text


def test_fix_propose_returns_fixed_payload(signing_material: SigningMaterial, auth_config: AuthConfig) -> None:
    fix_tool = create_fix_propose_tool(
        model_client=FakeModel(
            [
                "\n".join(
                    [
                        "--- a/src/app.py",
                        "+++ b/src/app.py",
                        "@@ -1,2 +1,2 @@",
                        " def run():",
                        "-    return 'bad'",
                        "+    return 'good'",
                    ]
                )
            ]
        ),
        validators=[_always_valid],
    )
    client = _client(signing_material=signing_material, auth_config=auth_config, fix_tool=fix_tool)
    payload = _call_fix(client, _token(signing_material))
    text = _content_text(payload)
    assert '"status": "fixed"' in text
    assert '"iterations": 1' in text
    assert "+    return 'good'" in text


def test_fix_propose_budget_exhausted(
    signing_material: SigningMaterial,
    auth_config: AuthConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_FIX_MAX_ITERATIONS", "2")
    fix_tool = create_fix_propose_tool(
        model_client=FakeModel(
            [
                "\n".join(
                    [
                        "--- a/src/app.py",
                        "+++ b/src/app.py",
                        "@@ -1,2 +1,2 @@",
                        " def run():",
                        "-    return 'bad'",
                        "+    return 'attempt_one'",
                    ]
                ),
                "\n".join(
                    [
                        "--- a/src/app.py",
                        "+++ b/src/app.py",
                        "@@ -1,2 +1,2 @@",
                        " def run():",
                        "-    return 'attempt_one'",
                        "+    return 'attempt_two'",
                    ]
                ),
            ]
        ),
        validators=[
            _validator_outcomes(
                [
                    ValidationResult(False, "tests red"),
                    ValidationResult(False, "tests still red"),
                ]
            )
        ],
    )
    client = _client(signing_material=signing_material, auth_config=auth_config, fix_tool=fix_tool)
    payload = _call_fix(client, _token(signing_material))
    text = _content_text(payload)
    assert '"status": "budget_exhausted"' in text
    assert '"iterations": 2' in text
    assert 'degraded: budget exhausted after 2 attempt(s)' in text


def test_fix_propose_rejects_out_of_scope_patch(
    signing_material: SigningMaterial,
    auth_config: AuthConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_FIX_MAX_ITERATIONS", "1")
    fix_tool = create_fix_propose_tool(
        model_client=FakeModel(
            [
                "\n".join(
                    [
                        "--- a/src/other.py",
                        "+++ b/src/other.py",
                        "@@ -1 +1 @@",
                        "-value = 1",
                        "+value = 2",
                    ]
                )
            ]
        ),
        validators=[_always_valid],
    )
    client = _client(signing_material=signing_material, auth_config=auth_config, fix_tool=fix_tool)
    payload = _call_fix(client, _token(signing_material))
    text = _content_text(payload)
    assert '"status": "invalid_patch"' in text
    assert '"final_diff": ""' in text


def test_fix_propose_repo_scope_rejection_returns_403(
    signing_material: SigningMaterial,
    auth_config: AuthConfig,
) -> None:
    fix_tool = create_fix_propose_tool(
        model_client=FakeModel([""]),
        validators=[_always_valid],
    )
    client = _client(signing_material=signing_material, auth_config=auth_config, fix_tool=fix_tool)
    response = client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "fix.propose", "arguments": {"repo_id": "other/repo", "diff": _base_diff()}},
        ),
        headers=_auth_headers(_token(signing_material, repos=["org/repo"])),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == -32003
