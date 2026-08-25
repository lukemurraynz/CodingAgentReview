import pytest

from harness.lifecycle import apply
from harness.models import Evidence, Finding, FindingCategory, Severity, Waiver
from harness.repository import WaiverImmutableError, ensure_waiver_immutable, reconcile_findings


def _finding(
    *,
    finding_id: str,
    dedup_key: str,
    change_id: str = "c1",
    repo_id: str = "org/repo",
    status: str = "candidate",
    title: str = "issue",
    path: str = "src/app.py",
    waiver: Waiver | None = None,
    reopened_from: str | None = None,
) -> Finding:
    return Finding(
        id=finding_id,
        change_id=change_id,
        repo_id=repo_id,
        category=FindingCategory.SECURITY,
        severity=Severity.HIGH,
        title=title,
        evidence=[Evidence(path=path, line_start=1)],
        dedup_key=dedup_key,
        status=status,
        waiver=waiver,
        reopened_from=reopened_from,
    )


class InMemoryFindingStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Finding] = {}

    async def put_finding(self, finding: Finding) -> None:
        existing = self._items.get((finding.repo_id, finding.id))
        ensure_waiver_immutable(existing, finding)
        self._items[(finding.repo_id, finding.id)] = finding

    async def get_findings(self, repo_id: str) -> list[Finding]:
        return [finding for (stored_repo, _), finding in self._items.items() if stored_repo == repo_id]


@pytest.mark.asyncio
async def test_duplicate_commit_dedupes_without_new_finding() -> None:
    store = InMemoryFindingStore()
    original = apply(_finding(finding_id="f1", dedup_key="v1:a"), "confirmed")
    await store.put_finding(original)

    result = await reconcile_findings(
        store,
        repo_id="org/repo",
        change_id="c2",
        candidate_findings=[_finding(finding_id="new-id", dedup_key="v1:a", change_id="c2")],
    )

    findings = await store.get_findings("org/repo")
    assert len(findings) == 1
    assert findings[0].id == "f1"
    assert findings[0].change_id == "c2"
    assert len(result.deduped) == 1
    assert not result.created


@pytest.mark.asyncio
async def test_reintroduced_resolved_finding_reopens_with_linkage() -> None:
    store = InMemoryFindingStore()
    resolved = apply(_finding(finding_id="f1", dedup_key="v1:a"), "confirmed")
    resolved = apply(resolved, "resolved")
    await store.put_finding(resolved)

    await reconcile_findings(
        store,
        repo_id="org/repo",
        change_id="c2",
        candidate_findings=[_finding(finding_id="fresh", dedup_key="v1:a", change_id="c2")],
    )

    findings = await store.get_findings("org/repo")
    assert findings[0].id == "f1"
    assert findings[0].status == "reopened"
    assert findings[0].reopened_from == "f1"
    assert findings[0].change_id == "c2"


@pytest.mark.asyncio
async def test_region_removal_stales_missing_open_finding() -> None:
    store = InMemoryFindingStore()
    keep = apply(_finding(finding_id="f1", dedup_key="v1:keep", path="src/keep.py"), "confirmed")
    remove = apply(_finding(finding_id="f2", dedup_key="v1:gone", path="src/remove.py"), "confirmed")
    await store.put_finding(keep)
    await store.put_finding(remove)

    result = await reconcile_findings(
        store,
        repo_id="org/repo",
        change_id="c2",
        candidate_findings=[_finding(finding_id="fresh", dedup_key="v1:keep", change_id="c2", path="src/keep.py")],
    )

    findings = {finding.id: finding for finding in await store.get_findings("org/repo")}
    assert findings["f1"].status == "confirmed"
    assert findings["f2"].status == "stale"
    assert [finding.id for finding in result.stale] == ["f2"]


@pytest.mark.asyncio
async def test_waived_finding_reopens_and_keeps_immutable_waiver() -> None:
    store = InMemoryFindingStore()
    waived = apply(_finding(finding_id="f1", dedup_key="v1:a"), "confirmed")
    waiver = Waiver(approver="lead", rationale="accepted")
    waived = apply(waived, "waived", waiver=waiver)
    await store.put_finding(waived)

    await reconcile_findings(
        store,
        repo_id="org/repo",
        change_id="c2",
        candidate_findings=[_finding(finding_id="fresh", dedup_key="v1:a", change_id="c2")],
    )

    reopened = (await store.get_findings("org/repo"))[0]
    assert reopened.status == "reopened"
    assert reopened.waiver == waiver


def test_waiver_mutation_attempts_fail_at_repository_layer() -> None:
    existing = apply(_finding(finding_id="f1", dedup_key="v1:a"), "confirmed")
    existing = apply(existing, "waived", waiver=Waiver(approver="lead", rationale="accepted"))
    mutated = Finding.model_validate(
        existing.model_dump(mode="python")
        | {
            "status": "reopened",
            "reopened_from": existing.id,
            "waiver": Waiver(approver="other", rationale="changed"),
        }
    )

    with pytest.raises(WaiverImmutableError, match="immutable"):
        ensure_waiver_immutable(existing, mutated)

    with pytest.raises(WaiverImmutableError, match="immutable"):
        ensure_waiver_immutable(existing, existing.model_copy(update={"waiver": None}))
