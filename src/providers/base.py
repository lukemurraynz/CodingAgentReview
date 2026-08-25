"""Git platform adapter contract (FR-001).

Concrete adapters (github/, azuredevops/) translate platform webhooks into
domain Changes and project results back. Transport/auth details belong in the
adapters; the control plane depends only on this protocol.
"""

from typing import Any, Protocol

from harness.models import Change, Finding


class ProviderAdapter(Protocol):
    name: str

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> Change | None:
        """Validate + translate an inbound webhook into a Change.

        Returns None for events this adapter does not trigger review on.
        Raises PermissionError on signature/auth failure.
        """
        ...

    async def fetch_diff(self, change: Change) -> str:
        """Unified diff text for the change."""
        ...

    async def post_annotations(self, change: Change, findings: list[Finding]) -> None:
        """Project findings onto the PR/commit as structured comments."""
        ...

    @staticmethod
    def event_envelope(source: str, subject: str, data: dict[str, Any]) -> dict[str, Any]:
        """Canonical event fields emitted for this change (FR-021)."""
        ...
