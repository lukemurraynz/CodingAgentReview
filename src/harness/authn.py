"""Shared Entra bearer-token validation helpers."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK

from harness.authz import Principal, principal_from_claims

HARNESS_ENTRA_TENANT_ID = "HARNESS_ENTRA_TENANT_ID"
HARNESS_ENTRA_AUDIENCE = "HARNESS_ENTRA_AUDIENCE"
HARNESS_ENTRA_CLIENT_ID = "HARNESS_ENTRA_CLIENT_ID"


class EntraAuthError(PermissionError):
    """Entra bearer authentication failed or was not configured."""


@dataclass(frozen=True, slots=True)
class EntraTokenConfig:
    """Configuration for validating Entra-issued JWT bearer tokens."""

    tenant_id: str
    audience: str
    extra_audience: str | None = None

    @classmethod
    def from_env(
        cls,
        *,
        tenant_env: str = HARNESS_ENTRA_TENANT_ID,
        audience_env: str = HARNESS_ENTRA_AUDIENCE,
        extra_audience_env: str = HARNESS_ENTRA_CLIENT_ID,
    ) -> EntraTokenConfig | None:
        tenant_id = (os.environ.get(tenant_env) or "").strip()
        audience = (os.environ.get(audience_env) or "").strip()
        extra_audience = (os.environ.get(extra_audience_env) or "").strip() or None
        if not tenant_id or not audience:
            return None
        return cls(tenant_id=tenant_id, audience=audience, extra_audience=extra_audience)

    @property
    def audiences(self) -> tuple[str, ...]:
        if self.extra_audience and self.extra_audience != self.audience:
            return (self.audience, self.extra_audience)
        return (self.audience,)

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"


class JwksFetcher(Protocol):
    async def fetch(self, jwks_url: str) -> dict[str, object]: ...


class HttpxJwksFetcher:
    """JWKS fetcher with a tiny in-memory TTL cache."""

    def __init__(self, *, ttl_seconds: int = 300) -> None:
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, object]]] = {}

    async def fetch(self, jwks_url: str) -> dict[str, object]:
        now = time.monotonic()
        cached = self._cache.get(jwks_url)
        if cached and cached[0] > now:
            return cached[1]
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise EntraAuthError("invalid JWKS payload")
        self._cache[jwks_url] = (now + self._ttl_seconds, payload)
        return payload


def principal_from_token_claims(claims: dict[str, object]) -> Principal:
    """Build a repo-scoped principal from validated token claims."""

    return principal_from_claims(claims)


def repo_ids_from_claims(claims: dict[str, object]) -> frozenset[str]:
    """Extract explicit repo grants from validated token claims."""

    repos_claim = claims.get("repos")
    if not isinstance(repos_claim, list):
        return frozenset()
    return frozenset(str(repo_id) for repo_id in repos_claim)


def _signing_jwk_from_jwks(jwks: dict[str, object], token: str) -> PyJWK:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise EntraAuthError("token header missing kid")
    key_set = jwt.PyJWKSet.from_dict(jwks)
    for key in key_set.keys:
        if key.key_id == kid:
            return key
    raise EntraAuthError("signing key not found")


def _bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise EntraAuthError("missing bearer token")
    return token


class EntraTokenVerifier:
    """Validate Entra bearer tokens against tenant JWKS."""

    def __init__(self, config: EntraTokenConfig, *, jwks_fetcher: JwksFetcher | None = None) -> None:
        self._config = config
        self._jwks_fetcher = jwks_fetcher or HttpxJwksFetcher()

    async def verify(self, token: str) -> dict[str, object]:
        try:
            jwks = await self._jwks_fetcher.fetch(self._config.jwks_url)
            key = _signing_jwk_from_jwks(jwks, token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=list(self._config.audiences),
                issuer=self._config.issuer,
                options={"require": ["exp", "nbf", "iss", "aud"]},
            )
        except InvalidTokenError as exc:
            raise EntraAuthError(f"invalid bearer token: {exc}") from exc
        if not isinstance(claims, dict):
            raise EntraAuthError("invalid bearer token: claims payload is not an object")
        return {str(key): value for key, value in claims.items()}

    async def verify_authorization(self, authorization: str) -> dict[str, object]:
        return await self.verify(_bearer_token(authorization))

    async def verify_principal(self, token: str) -> Principal:
        return principal_from_token_claims(await self.verify(token))

    async def verify_authorization_principal(self, authorization: str) -> Principal:
        return principal_from_token_claims(await self.verify_authorization(authorization))
