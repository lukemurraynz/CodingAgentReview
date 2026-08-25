"""Entra bearer-token validation and repo scoping for the MCP host."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Request

from harness.authn import EntraAuthError, EntraTokenConfig, EntraTokenVerifier, JwksFetcher, principal_from_token_claims
from harness.authz import Principal, RepoAccessDenied

_TENANT_ID_ENV = "HARNESS_MCP_ENTRA_TENANT_ID"
_AUDIENCE_ENV = "HARNESS_MCP_ENTRA_AUDIENCE"
_CLIENT_ID_ENV = "HARNESS_MCP_ENTRA_CLIENT_ID"


class MpcAuthError(PermissionError):
    """Authentication failed or configuration is incomplete."""


@dataclass(frozen=True, slots=True)
class AuthConfig:
    tenant_id: str
    audience: str
    client_id: str | None
    issuer: str | None = None
    jwks_url: str | None = None

    @classmethod
    def from_env(cls) -> AuthConfig | None:
        tenant = (os.environ.get(_TENANT_ID_ENV) or "").strip()
        audience = (os.environ.get(_AUDIENCE_ENV) or "").strip()
        client_id = (os.environ.get(_CLIENT_ID_ENV) or "").strip() or None
        if not tenant or not audience:
            return None
        verifier_config = EntraTokenConfig(tenant_id=tenant, audience=audience, extra_audience=client_id)
        return cls(
            tenant_id=tenant,
            audience=audience,
            client_id=client_id,
            issuer=verifier_config.issuer,
            jwks_url=verifier_config.jwks_url,
        )

    @property
    def audiences(self) -> tuple[str, ...]:
        if self.client_id and self.client_id != self.audience:
            return (self.audience, self.client_id)
        return (self.audience,)

    def to_verifier_config(self) -> EntraTokenConfig:
        return EntraTokenConfig(
            tenant_id=self.tenant_id,
            audience=self.audience,
            extra_audience=self.client_id,
        )


class BearerTokenAuthenticator:
    def __init__(self, *, config: AuthConfig | None = None, jwks_fetcher: JwksFetcher | None = None) -> None:
        self._config = config
        self._jwks_fetcher = jwks_fetcher

    @property
    def config(self) -> AuthConfig | None:
        return self._config or AuthConfig.from_env()

    async def authenticate(self, request: Request) -> Principal:
        config = self.config
        if config is None:
            raise MpcAuthError(
                f"MCP auth not configured; set {_TENANT_ID_ENV} and {_AUDIENCE_ENV}"
            )
        try:
            verifier = EntraTokenVerifier(config.to_verifier_config(), jwks_fetcher=self._jwks_fetcher)
            claims = await verifier.verify_authorization(request.headers.get("authorization", ""))
        except EntraAuthError as exc:
            raise MpcAuthError(str(exc)) from exc
        return principal_from_token_claims(claims)


def require_repo(principal: Principal, repo_id: str, *, write: bool = False) -> None:
    if not principal.can_access(repo_id, require_write=write):
        raise RepoAccessDenied(f"principal {principal.oid} lacks access to {repo_id}")
