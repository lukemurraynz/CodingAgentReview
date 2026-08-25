"""Credential adapter: forces the correct Entra audience for Foundry's
model-inference surface (https://ai.azure.com), which azure-ai-inference
does not derive automatically from the endpoint.
"""

from __future__ import annotations

from typing import Any

AI_FOUNDRY_SCOPE = "https://ai.azure.com/.default"


class ScopedAsyncCredential:
    """Wraps an AsyncTokenCredential, always requesting the Foundry scope."""

    def __init__(self, inner: Any, scope: str = AI_FOUNDRY_SCOPE) -> None:
        self._inner = inner
        self._scope = scope

    async def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return await self._inner.get_token(self._scope, **kwargs)

    async def close(self) -> None:
        await self._inner.close()

    async def __aenter__(self) -> ScopedAsyncCredential:
        return self

    async def __aexit__(self, exc_type: Any = None, exc_value: Any = None, traceback: Any = None) -> None:
        await self.close()
