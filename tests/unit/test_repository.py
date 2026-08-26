import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

import pytest

from harness.healthcheck import probe_repository
from harness.models import Evidence, Finding, FindingCategory, ReviewRun, Severity
from harness.repository import HarnessRepository, reconcile_findings
from providers.formatting import build_comment_body
from worker.persistence import persist


def _finding(*, repo_id: str = "org/repo", title: str = "issue", detail: str = "") -> Finding:
    return Finding(
        id="f-1",
        change_id="chg-1",
        repo_id=repo_id,
        category=FindingCategory.SECURITY,
        severity=Severity.HIGH,
        title=title,
        detail=detail,
        evidence=[Evidence(path="src/app.py", line_start=7, note=detail)],
        dedup_key="dedup:f-1",
    )


class _AsyncListIterator:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> "_AsyncListIterator":
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _RecordingContainer:
    def __init__(self, *, items: list[dict[str, object]] | None = None) -> None:
        self.items = list(items or [])
        self.upserts: list[dict[str, object]] = []
        self.created: list[dict[str, object]] = []
        self.deleted: list[tuple[str, str]] = []
        self.read_all_partition_keys: list[str] = []
        self.query_partition_keys: list[str] = []

    def read_all_items(self, *, partition_key: str) -> _AsyncListIterator:
        self.read_all_partition_keys.append(partition_key)
        return _AsyncListIterator(self.items)

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, object]] | None = None,
        partition_key: str,
    ) -> _AsyncListIterator:
        del query, parameters
        self.query_partition_keys.append(partition_key)
        return _AsyncListIterator([])

    async def upsert_item(self, item: dict[str, object]) -> None:
        self.upserts.append(item)

    async def create_item(self, item: dict[str, object]) -> None:
        for current in [*self.items, *self.upserts, *self.created]:
            if current.get("id") == item.get("id") and current.get("pk") == item.get("pk"):
                raise CosmosResourceExistsError("exists")
        self.created.append(item)

    async def delete_item(self, *, item: str, partition_key: str) -> None:
        self.deleted.append((item, partition_key))
        for collection in (self.items, self.upserts, self.created):
            for index, current in enumerate(collection):
                if current.get("id") == item and current.get("pk") == partition_key:
                    collection.pop(index)
                    return
        raise CosmosResourceNotFoundError("missing")

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, object]:
        for current in [*self.upserts, *self.created, *self.items]:
            current_partition = current.get("repo_id", current.get("change_id", current.get("pk")))
            if current["id"] == item and current_partition == partition_key:
                return current
        raise CosmosResourceNotFoundError("missing")


class CosmosResourceNotFoundError(Exception):
    pass


class CosmosResourceExistsError(Exception):
    pass


class _SessionRepository(HarnessRepository):
    def __init__(
        self,
        findings: _RecordingContainer | None = None,
        runs: _RecordingContainer | None = None,
        dedups: _RecordingContainer | None = None,
    ) -> None:
        super().__init__(database="harness")
        self.findings = findings or _RecordingContainer()
        self.runs = runs or _RecordingContainer()
        self.dedups = dedups or _RecordingContainer()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[tuple[_RecordingContainer, _RecordingContainer]]:
        yield self.findings, self.runs

    @asynccontextmanager
    async def _dedup_session(self) -> AsyncIterator[_RecordingContainer]:
        yield self.dedups


class _ScopedStore:
    def __init__(self) -> None:
        self.repo_ids: list[str] = []
        self._items: list[Finding] = []

    async def put_finding(self, finding: Finding) -> None:
        self.repo_ids.append(finding.repo_id)
        self._items.append(finding)

    async def get_findings(self, repo_id: str) -> list[Finding]:
        return [finding for finding in self._items if finding.repo_id == repo_id]


class _SlowHealthcheck:
    @staticmethod
    def probe_cancellation_seconds() -> int:
        return 0

    async def healthcheck(self) -> None:
        await asyncio.sleep(0.25)


@pytest.mark.asyncio
async def test_repository_client_uses_request_timeout_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeCredential:
        pass

    class FakeClient:
        def __init__(self, endpoint: str, *, credential: object, timeout: int, connection_timeout: int) -> None:
            captured.update(
                {
                    "endpoint": endpoint,
                    "credential": credential,
                    "timeout": timeout,
                    "connection_timeout": connection_timeout,
                }
            )

        def get_database_client(self, database_name: str) -> object:
            del database_name
            return self

        def get_container_client(self, container_name: str) -> _RecordingContainer:
            del container_name
            return _RecordingContainer()

        async def close(self) -> None:
            captured["closed"] = True

    cosmos_module: Any = ModuleType("azure.cosmos.aio")
    cosmos_module.CosmosClient = FakeClient
    identity_module: Any = ModuleType("azure.identity.aio")
    identity_module.DefaultAzureCredential = FakeCredential
    monkeypatch.setitem(sys.modules, "azure.cosmos.aio", cosmos_module)
    monkeypatch.setitem(sys.modules, "azure.identity.aio", identity_module)
    monkeypatch.setenv("HARNESS_COSMOS_ENDPOINT", "https://cosmos.example")

    repository = HarnessRepository()
    async with repository._session():
        pass

    assert captured["endpoint"] == "https://cosmos.example"
    assert captured["timeout"] == repository.request_timeout_seconds()
    assert captured["connection_timeout"] == repository.request_timeout_seconds()
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_probe_repository_cancels_slow_healthcheck_early() -> None:
    started = time.perf_counter()
    result = await probe_repository(_SlowHealthcheck(), timeout_seconds=0.01)
    elapsed = time.perf_counter() - started

    assert result.ok is False
    assert result.detail == "timeout after 0.01s"
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_repository_queries_stay_partition_scoped() -> None:
    findings = _RecordingContainer(
        items=[
            _finding().model_dump(mode="json"),
        ]
    )
    runs = _RecordingContainer()
    repository = _SessionRepository(findings=findings, runs=runs)

    await repository.get_findings("org/repo")
    await repository.query_runs_for_change("chg-1")

    assert findings.read_all_partition_keys == ["org/repo"]
    assert runs.query_partition_keys == ["chg-1"]


@pytest.mark.asyncio
async def test_reconcile_never_writes_outside_requested_repo() -> None:
    store = _ScopedStore()
    candidate = _finding(repo_id="other/repo")

    await reconcile_findings(
        store,
        repo_id="org/repo",
        change_id="chg-1",
        candidate_findings=[candidate],
    )

    assert store.repo_ids == ["org/repo"]


@pytest.mark.asyncio
async def test_redaction_end_to_end_across_persistence_events_and_comment_body() -> None:
    secret_aws = "AKIAIOSFODNN7EXAMPLE"
    secret_gh = "ghp_" + "a" * 36
    secret_account = "AccountKey=" + "x" * 60
    synthetic_diff = f"- token {secret_aws}\n+ client_secret={secret_gh}\n# {secret_account}"
    finding = _finding(
        title=f"leak {secret_aws}",
        detail=f"detail {synthetic_diff}",
    )
    run = ReviewRun(id="run-1", change_id="chg-1", head_sha="abc")
    repository = _SessionRepository()

    await persist(run, [finding], repository=repository)

    emitted: list[dict[str, Any]] = []

    async def _capture(event: Any) -> None:
        emitted.append(event.model_dump(mode="json"))

    await reconcile_findings(
        _ScopedStore(),
        repo_id="org/repo",
        change_id="chg-1",
        candidate_findings=[finding],
        emitter=__import__("harness.events", fromlist=["EventEmitter"]).EventEmitter(_capture),
    )

    comment_body = build_comment_body([finding])
    persisted_payload = json.dumps(repository.findings.upserts, sort_keys=True)
    emitted_payload = json.dumps(emitted, sort_keys=True)

    for secret in (secret_aws, secret_gh, secret_account):
        assert secret not in persisted_payload
        assert secret not in emitted_payload
        assert secret not in comment_body
    assert "[REDACTED:" in persisted_payload
    assert "[REDACTED:" in comment_body


@pytest.mark.asyncio
async def test_claim_delivery_creates_unique_record() -> None:
    repository = _SessionRepository()

    claimed = await repository.claim_delivery(("org/repo", "7", "headsha"), ttl_seconds=10, now=100.0)

    assert claimed is True
    assert repository.dedups.created[0]["id"] == "7:headsha"
    assert repository.dedups.created[0]["pk"] == "org/repo"
    assert repository.dedups.created[0]["ttl"] == 10


@pytest.mark.asyncio
async def test_claim_delivery_rejects_live_duplicate() -> None:
    repository = _SessionRepository(
        dedups=_RecordingContainer(
            items=[
                {
                    "id": "7:headsha",
                    "pk": "org/repo",
                    "expires_at": "1970-01-01T00:03:20+00:00",
                }
            ]
        )
    )

    claimed = await repository.claim_delivery(("org/repo", "7", "headsha"), ttl_seconds=10, now=100.0)

    assert claimed is False


@pytest.mark.asyncio
async def test_claim_delivery_replaces_expired_record() -> None:
    dedups = _RecordingContainer(
        items=[
            {
                "id": "7:headsha",
                "pk": "org/repo",
                "expires_at": "1970-01-01T00:01:35+00:00",
            }
        ]
    )
    repository = _SessionRepository(dedups=dedups)

    claimed = await repository.claim_delivery(("org/repo", "7", "headsha"), ttl_seconds=10, now=100.0)

    assert claimed is True
    assert dedups.deleted == [("7:headsha", "org/repo")]


@pytest.mark.asyncio
async def test_release_delivery_claim_is_idempotent() -> None:
    repository = _SessionRepository()

    await repository.release_delivery_claim(("org/repo", "7", "headsha"))

    assert repository.dedups.deleted == [("7:headsha", "org/repo")]
