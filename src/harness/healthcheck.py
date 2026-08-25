"""Small async health-check helpers for hosts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


class SupportsHealthcheck(Protocol):
    @staticmethod
    def probe_cancellation_seconds() -> int: ...

    async def healthcheck(self) -> None: ...


@dataclass(frozen=True, slots=True)
class HealthcheckResult:
    ok: bool
    detail: str


async def run_with_timeout(
    operation: Callable[[], Awaitable[None]],
    *,
    timeout_seconds: float,
) -> HealthcheckResult:
    try:
        await asyncio.wait_for(operation(), timeout=timeout_seconds)
    except TimeoutError:
        return HealthcheckResult(ok=False, detail=f"timeout after {timeout_seconds:g}s")
    except Exception as exc:
        return HealthcheckResult(ok=False, detail=f"{exc.__class__.__name__}: {exc}")
    return HealthcheckResult(ok=True, detail="ok")


async def probe_repository(
    repository: SupportsHealthcheck,
    *,
    timeout_seconds: float | None = None,
) -> HealthcheckResult:
    budget = float(timeout_seconds or repository.probe_cancellation_seconds())
    return await run_with_timeout(repository.healthcheck, timeout_seconds=budget)
