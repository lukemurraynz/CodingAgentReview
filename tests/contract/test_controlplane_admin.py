"""Controlplane admin waiver route contract tests (FR-016 / T058)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from controlplane import create_app
from harness.lifecycle import apply
from harness.models import Evidence, Finding, FindingCategory, Severity, Waiver

ADMIN_TOKEN = "admin-secret"
REPO_ID = "org/repo"


def _finding(*, finding_id: str, status: str = "confirmed", waiver: Waiver | None = None) -> Finding:
    return Finding(
        id=finding_id,
        change_id="change-1",
        repo_id=REPO_ID,
        category=FindingCategory.SECURITY,
        severity=Severity.HIGH,
        title="missing auth test",
        evidence=[Evidence(path="src/app.py", line_start=7)],
        dedup_key=f"dedup:{finding_id}",
        status=status,
        waiver=waiver,
        reopened_from=finding_id if status == "reopened" else None,
    )


class InMemoryAdminRepository:
    def __init__(self, findings: list[Finding] | None = None) -> None:
        self._items = {(finding.repo_id, finding.id): finding for finding in findings or []}

    async def get_finding(self, repo_id: str, finding_id: str) -> Finding | None:
        return self._items.get((repo_id, finding_id))

    async def get_findings(self, repo_id: str) -> list[Finding]:
        return [finding for (stored_repo, _), finding in self._items.items() if stored_repo == repo_id]

    async def put_finding(self, finding: Finding) -> None:
        self._items[(finding.repo_id, finding.id)] = finding


def _headers(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client_factory(monkeypatch):
    monkeypatch.setenv("HARNESS_ADMIN_TOKEN", ADMIN_TOKEN)

    def _make(repository: InMemoryAdminRepository) -> TestClient:
        return TestClient(create_app(repository=repository))

    return _make


def test_create_waiver_persists_and_returns_record(client_factory) -> None:
    repository = InMemoryAdminRepository([_finding(finding_id="f-1")])
    client = client_factory(repository)

    response = client.post(
        f"/admin/repos/{REPO_ID}/findings/f-1/waiver",
        headers=_headers(),
        json={"approver": "lead@contoso", "rationale": "false positive in generated path"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "findingId": "f-1",
        "repoId": REPO_ID,
        "status": "waived",
        "approver": "lead@contoso",
        "rationale": "false positive in generated path",
        "decidedAt": response.json()["decidedAt"],
    }

    stored = repository._items[(REPO_ID, "f-1")]
    assert stored.status == "waived"
    assert stored.waiver is not None
    assert stored.waiver.approver == "lead@contoso"
    assert stored.waiver.rationale == "false positive in generated path"


def test_duplicate_waiver_is_rejected_without_overwrite(client_factory) -> None:
    existing = apply(
        _finding(finding_id="f-2"),
        "waived",
        waiver=Waiver(approver="first@contoso", rationale="accepted risk"),
    )
    repository = InMemoryAdminRepository([existing])
    client = client_factory(repository)

    response = client.post(
        f"/admin/repos/{REPO_ID}/findings/f-2/waiver",
        headers=_headers(),
        json={"approver": "second@contoso", "rationale": "overwrite attempt"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "waiver already exists for finding"}
    assert repository._items[(REPO_ID, "f-2")].waiver == existing.waiver


def test_admin_auth_rejects_missing_and_invalid_tokens(client_factory) -> None:
    client = client_factory(InMemoryAdminRepository([_finding(finding_id="f-3")]))

    missing = client.get(f"/admin/repos/{REPO_ID}/waivers")
    invalid = client.get(f"/admin/repos/{REPO_ID}/waivers", headers=_headers("wrong-token"))

    assert missing.status_code == 401
    assert missing.json() == {"detail": "missing bearer token"}
    assert invalid.status_code == 403
    assert invalid.json() == {"detail": "invalid admin token"}


def test_admin_auth_fails_closed_when_token_unset(monkeypatch) -> None:
    monkeypatch.delenv("HARNESS_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HARNESS_ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("HARNESS_ENTRA_AUDIENCE", raising=False)
    monkeypatch.delenv("HARNESS_ENTRA_CLIENT_ID", raising=False)
    client = TestClient(create_app(repository=InMemoryAdminRepository([_finding(finding_id="f-4")])))

    response = client.get(f"/admin/repos/{REPO_ID}/waivers", headers=_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": "admin auth not configured; set HARNESS_ADMIN_TOKEN"}


def test_list_and_get_waivers_return_persisted_records(client_factory) -> None:
    current = apply(
        _finding(finding_id="f-5"),
        "waived",
        waiver=Waiver(approver="lead@contoso", rationale="accepted"),
    )
    reopened = apply(
        apply(
            _finding(finding_id="f-6"),
            "waived",
            waiver=Waiver(approver="owner@contoso", rationale="temporary exception"),
        ),
        "reopened",
        reopened_from="f-6",
    )
    repository = InMemoryAdminRepository([current, reopened, _finding(finding_id="f-7")])
    client = client_factory(repository)

    list_response = client.get(f"/admin/repos/{REPO_ID}/waivers", headers=_headers())
    get_response = client.get(f"/admin/repos/{REPO_ID}/waivers/f-6", headers=_headers())

    assert list_response.status_code == 200
    assert [item["findingId"] for item in list_response.json()] == ["f-6", "f-5"]
    assert get_response.status_code == 200
    assert get_response.json() == {
        "findingId": "f-6",
        "repoId": REPO_ID,
        "status": "reopened",
        "approver": "owner@contoso",
        "rationale": "temporary exception",
        "decidedAt": get_response.json()["decidedAt"],
    }
