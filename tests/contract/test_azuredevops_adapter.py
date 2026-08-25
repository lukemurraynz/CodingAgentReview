"""Contract tests: Azure DevOps adapter auth/parsing."""

import base64
import json

import httpx
import pytest

from harness.models import Change, ChangeType, Finding, GitProvider, RiskLevel, RunStatus
from providers.azuredevops import AzureDevOpsAdapter, LinkedWorkItemCompleteness, WorkItemSummary
from providers.base import AnnotationReport, GateVerdict, ProviderHttpError

PAT = "testpat123"
ADAPTER = AzureDevOpsAdapter()


def _auth() -> dict[str, str]:
    token = base64.b64encode(f":{PAT}".encode()).decode()
    return {"authorization": f"Basic {token}"}


def _pr_payload() -> bytes:
    return json.dumps(
        {
            "eventType": "git.pullrequest.created",
            "publisher": {"id": "my-org"},
            "resource": {
                "pull_request": {
                    "pullRequestId": 42,
                    "lastMergeCommit": {"commitId": "m1"},
                    "mergeBaseCommit": {"commitId": "b1"},
                },
                "repository": {
                    "name": "infra",
                    "project": {"name": "Platform"},
                },
            },
            "resourceContainers": {"account": {"id": "0000"}},
        }
    ).encode()


class TestAuth:
    def test_valid_pat_parses(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        change = ADAPTER.parse_webhook(_auth(), _pr_payload())
        assert change is not None
        assert change.change_type == ChangeType.PULL_REQUEST
        assert change.pr_number == 42
        assert change.repo_id == "my-org.Platform/infra"

    def test_missing_pat_fails_closed(self, monkeypatch):
        monkeypatch.delenv("HARNESS_ADO_PAT", raising=False)
        with pytest.raises(PermissionError):
            ADAPTER.parse_webhook(_auth(), _pr_payload())

    def test_wrong_pat_rejected(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        bad = base64.b64encode(b":wrong").decode()
        with pytest.raises(PermissionError):
            ADAPTER.parse_webhook({"authorization": f"Basic {bad}"}, _pr_payload())


class TestRepoIdConvention:
    def test_split_round_trip(self):
        assert ADAPTER._split_repo("org.Project/repo") == ("org", "Project", "repo")

    def test_malformed_repo_id_raises(self):
        with pytest.raises(ValueError, match="org"):
            ADAPTER._split_repo("just-a-name")


class TestPushRouting:
    def test_push_maps_to_commit(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        body = json.dumps(
            {
                "eventType": "git.push",
                "publisher": {"id": "my-org"},
                "resource": {
                    "refUpdates": [{"newObjectId": "deadbeef"}],
                    "repository": {"name": "infra", "project": {"name": "Platform"}},
                },
                "resourceContainers": {"account": {"id": "x"}},
            }
        ).encode()
        change = ADAPTER.parse_webhook(_auth(), body)
        assert change is not None and change.change_type == ChangeType.COMMIT
        assert change.head_sha == "deadbeef"


class TestHttpContracts:
    async def test_pr_fetch_diff_url_and_auth(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, request=request, json={"changeDiffs": [{"diff": {"content": "x"}}]})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra#42",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="m1",
            base_sha="b1",
            pr_number=42,
        )
        await ADAPTER.fetch_diff(change)
        assert "pullrequests/42/iterations" in str(captured["url"])
        assert str(captured["auth"]).startswith("Basic ")

    async def test_commit_fetch_diff_url(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, request=request, json={"changeDiffs": []})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra@deadbeef",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.COMMIT,
            head_sha="deadbeef",
            base_sha="beadfeed",
        )
        await ADAPTER.fetch_diff(change)
        assert "diffs/commits" in captured["url"]
        assert "baseVersion=beadfeed" in captured["url"]
        assert "targetVersion=deadbeef" in captured["url"]

    async def test_http_failure_becomes_typed_exception(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request, text="down")

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra@deadbeef",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.COMMIT,
            head_sha="deadbeef",
            base_sha="beadfeed",
        )
        with pytest.raises(ProviderHttpError, match="azure_devops:fetch_diff"):
            await ADAPTER.fetch_diff(change)

    async def test_post_annotations_payload_shape(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = request.content.decode()
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, request=request, json={"id": 1})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra#42",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="m1",
            base_sha="b1",
            pr_number=42,
        )
        report = AnnotationReport(
            gate=GateVerdict(
                status="pass",
                blocking_findings=0,
                risk_floor=RiskLevel.MEDIUM,
                risk_level=RiskLevel.MEDIUM,
                reason="no blocking findings",
            ),
            run_status=RunStatus.COMPLETED,
            coverage="full",
            lens_coverage="lenses: 1 declared, 1 reported, 0 unavailable",
        )
        finding = Finding.model_validate(
            {
                "id": "f1",
                "change_id": "c1",
                "repo_id": "org/repo",
                "category": "security",
                "severity": "high",
                "title": "bad auth",
                "detail": "detail",
                "evidence": [{"path": "src/a.py", "line_start": 4}],
                "dedup_key": "d1",
            }
        )
        await ADAPTER.post_annotations(change, [finding], report)
        assert "threads?api-version=7.1" in str(captured["url"])
        assert "Gate verdict" in str(captured["body"])
        assert "threadContext" in str(captured["body"])
        assert str(captured["auth"]).startswith("Basic ")

    async def test_upsert_summary_comment_edits_existing_thread(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        calls: list[tuple[str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, str(request.url), request.content.decode()))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    request=request,
                    json={"value": [{"id": 7, "comments": [{"content": "<!-- harness-review-summary -->\nold"}]}]},
                )
            return httpx.Response(200, request=request, json={"id": 7})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra#42",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="m1",
            base_sha="b1",
            pr_number=42,
        )
        report = AnnotationReport(
            gate=GateVerdict(
                status="degraded",
                blocking_findings=0,
                risk_floor=RiskLevel.MEDIUM,
                risk_level=RiskLevel.MEDIUM,
                reason="partial lens coverage",
            ),
            run_status=RunStatus.DEGRADED,
            coverage="partial_explicit",
            lens_coverage="lenses: 2 declared, 2 reported, 1 unavailable",
        )
        await ADAPTER.upsert_summary_comment(change, [], report)
        assert calls[0][0] == "GET"
        assert calls[1][0] == "PATCH"
        assert "/threads/7/comments/1" in calls[1][1]

    async def test_fetch_linked_workitems_incomplete(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)
        calls: list[tuple[str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, str(request.url), request.content.decode()))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    request=request,
                    json={"value": [{"id": "101"}, {"id": 102}]},
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "value": [
                        {"id": 101, "fields": {"System.Title": "Closed item", "System.State": "Closed"}},
                        {"id": 102, "fields": {"System.Title": "Active item", "System.State": "Active"}},
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra#42",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="m1",
            base_sha="b1",
            pr_number=42,
        )

        result = await ADAPTER.fetch_linked_workitems(change)

        assert result == LinkedWorkItemCompleteness(
            linked_items=(
                WorkItemSummary(id=101, title="Closed item", state="Closed"),
                WorkItemSummary(id=102, title="Active item", state="Active"),
            ),
            complete=False,
            open_states=("Active",),
        )
        assert calls[0][0] == "GET"
        assert "pullrequests/42/workitems" in calls[0][1]
        assert calls[1][0] == "POST"
        assert "workitemsbatch" in calls[1][1]
        assert '"ids":[101,102]' in calls[1][2].replace(" ", "")

    async def test_fetch_linked_workitems_without_links_is_unknown(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, json={"value": []})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra#42",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="m1",
            base_sha="b1",
            pr_number=42,
        )

        result = await ADAPTER.fetch_linked_workitems(change)

        assert result == LinkedWorkItemCompleteness((), None)

    async def test_fetch_linked_workitems_http_failure_becomes_typed_exception(self, monkeypatch):
        monkeypatch.setenv("HARNESS_ADO_PAT", PAT)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request, text="down")

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="my-org.Platform/infra#42",
            provider=GitProvider.AZURE_DEVOPS,
            repo_id="my-org.Platform/infra",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="m1",
            base_sha="b1",
            pr_number=42,
        )

        with pytest.raises(ProviderHttpError, match="azure_devops:fetch_linked_workitems"):
            await ADAPTER.fetch_linked_workitems(change)
