"""Finding lifecycle state machine (FR-014).

Transitions:
    candidate  -> confirmed | stale
    confirmed  -> waived | resolved | stale
    waived     -> reopened
    resolved   -> reopened
    stale      -> reopened | resolved
    reopened   -> waived | resolved | stale

Rules:
- Moving to ``waived`` requires a Waiver record; leaving waived clears nothing —
  history is preserved on the Finding, waivers are immutable once written (FR-016).
- Moving to ``reopened`` requires ``reopened_from`` referencing the original id.
"""

from datetime import UTC, datetime

from .models.finding import Finding, Waiver

_ALLOWED: dict[str, set[str]] = {
    "candidate": {"confirmed", "stale"},
    "confirmed": {"waived", "resolved", "stale"},
    "waived": {"reopened"},
    "resolved": {"reopened"},
    "stale": {"reopened", "resolved"},
    "reopened": {"waived", "resolved", "stale"},
}


class LifecycleError(ValueError):
    pass


def can_transition(finding: Finding, to_status: str) -> bool:
    return to_status in _ALLOWED.get(finding.status, set())


def transition(
    finding: Finding,
    to_status: str,
 *,
    waiver: Waiver | None = None,
    reopened_from: str | None = None,
) -> Finding:
    return apply(finding, to_status, waiver=waiver, reopened_from=reopened_from)


def apply(
    finding: Finding,
    to_status: str,
    *,
    waiver: Waiver | None = None,
    reopened_from: str | None = None,
) -> Finding:
    """Return a new validated Finding moved to ``to_status``.

    Re-validation through ``model_validate(model_dump())`` is deliberate:
    pydantic v2 ``model_copy(update=...)`` skips validators (known-pitfall).
    """
    if not can_transition(finding, to_status):
        raise LifecycleError(f"illegal transition {finding.status} -> {to_status}")

    data = finding.model_dump()
    data["status"] = to_status
    existing_waiver = finding.waiver.model_dump() if finding.waiver is not None else None
    if to_status == "waived":
        if waiver is None and finding.waiver is None:
            raise LifecycleError("transition to 'waived' requires a waiver record")
        if finding.waiver is not None and waiver is not None and waiver != finding.waiver:
            raise LifecycleError("waiver record is immutable once written")
        if waiver is not None:
            data["waiver"] = waiver.model_dump()
        else:
            data["waiver"] = existing_waiver
    else:
        if waiver is not None:
            if finding.waiver is None:
                raise LifecycleError("waiver record can only be created when transitioning to 'waived'")
            if waiver != finding.waiver:
                raise LifecycleError("waiver record is immutable once written")
        data["waiver"] = existing_waiver
    if to_status == "reopened":
        origin = reopened_from or finding.reopened_from or finding.id
        if not origin:
            raise LifecycleError("reopen requires an originating finding id")
        data["reopened_from"] = origin
    data["updated_at"] = datetime.now(UTC)
    return Finding.model_validate(data)
