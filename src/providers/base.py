"""Git platform adapter contract (FR-001).

Concrete adapters (github/, azuredevops/) translate platform webhooks into
domain Changes and project results back. Transport/auth details belong in the
adapters; the control plane depends only on this protocol.
"""

from dataclasses import dataclass
from typing import Any, Final, Protocol

from harness.models import Change, Finding, RiskLevel, RunStatus


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Merge-gate result projected back to the provider surface (FR-013)."""

    status: str
    blocking_findings: int
    risk_floor: RiskLevel
    risk_level: RiskLevel
    reason: str
    acknowledgement_required: bool = False
    acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationReport:
    """Run summary that accompanies annotations/comments."""

    gate: GateVerdict
    run_status: RunStatus
    coverage: str
    lens_coverage: str = ""
    workitem_completeness: str = ""
    not_flagged: tuple[str, ...] = ()
    degraded_reasons: tuple[str, ...] = ()


PROMPT_VERSION: Final = "1"


class ProviderHttpError(RuntimeError):
    """Typed transport/status failure for provider HTTP operations."""

    def __init__(
        self,
        provider: str,
        operation: str,
        *,
        url: str,
        status_code: int | None = None,
        detail: str = "",
    ) -> None:
        self.provider = provider
        self.operation = operation
        self.url = url
        self.status_code = status_code
        self.detail = detail
        status_text = f" status={status_code}" if status_code is not None else ""
        detail_text = f" detail={detail}" if detail else ""
        super().__init__(f"{provider}:{operation} failed{status_text} url={url}{detail_text}")


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

    async def post_annotations(
        self,
        change: Change,
        findings: list[Finding],
        report: AnnotationReport | None = None,
    ) -> None:
        """Project findings onto the PR/commit as structured comments."""
        ...

    async def upsert_summary_comment(
        self,
        change: Change,
        findings: list[Finding],
        report: AnnotationReport | None = None,
    ) -> None:
        """Create or update the single provider summary comment for a review run."""
        ...

    @staticmethod
    def event_envelope(source: str, subject: str, data: dict[str, Any]) -> dict[str, Any]:
        """Canonical event fields emitted for this change (FR-021)."""
        ...
