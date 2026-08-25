"""Contract tests: GitHub adapter signature/parsing/diff URL construction."""

import hashlib
import hmac
import json

import httpx
import pytest

from harness.models import Change, ChangeType, GitProvider, RiskLevel, RunStatus
from providers.base import AnnotationReport, GateVerdict, ProviderHttpError
from providers.github import GitHubAdapter

SECRET = "test-secret"
ADAPTER = GitHubAdapter()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _pr_payload(action: str = "opened") -> bytes:
    payload = {
        "action": action,
        "number": 12,
        "pull_request": {
            "number": 12,
            "head": {"sha": "aaaa1111"},
            "base": {"sha": "bbbb2222"},
        },
        "repository": {"full_name": "org/repo"},
    }
    return json.dumps(payload).encode()


class TestSignature:
    def test_valid_signature_parses(self, monkeypatch):
        monkeypatch.setenv("HARNESS_GITHUB_WEBHOOK_SECRET", SECRET)
        body = _pr_payload()
        headers = {"x-hub-signature-256": _sign(body), "x-github-event": "pull_request"}
        change = ADAPTER.parse_webhook(headers, body)
        assert change is not None
        assert change.change_type == ChangeType.PULL_REQUEST
        assert change.repo_id == "org/repo"
        assert change.pr_number == 12
        assert change.head_sha == "aaaa1111"
        assert change.base_sha == "bbbb2222"

    def test_bad_signature_rejected(self, monkeypatch):
        monkeypatch.setenv("HARNESS_GITHUB_WEBHOOK_SECRET", SECRET)
        headers = {"x-hub-signature-256": "sha256=" + "0" * 64, "x-github-event": "pull_request"}
        with pytest.raises(PermissionError):
            ADAPTER.parse_webhook(headers, _pr_payload())

    def test_missing_secret_fails_closed(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_WEBHOOK_SECRET", raising=False)
        with pytest.raises(PermissionError):
            ADAPTER.parse_webhook({"x-hub-signature-256": "x"}, _pr_payload())


class TestEventRouting:
    @pytest.mark.parametrize("event", ["issues", "star", "ping"])
    def test_irrelevant_events_return_none(self, monkeypatch, event):
        monkeypatch.setenv("HARNESS_GITHUB_WEBHOOK_SECRET", SECRET)
        body = json.dumps({"action": "created", "repository": {"full_name": "o/r"}}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert ADAPTER.parse_webhook({"x-hub-signature-256": sig, "x-github-event": event}, body) is None

    def test_push_maps_to_commit_change(self, monkeypatch):
        monkeypatch.setenv("HARNESS_GITHUB_WEBHOOK_SECRET", SECRET)
        body = json.dumps({"after": "c0ffee1", "repository": {"full_name": "org/repo"}}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        change = ADAPTER.parse_webhook({"x-hub-signature-256": sig, "x-github-event": "push"}, body)
        assert change is not None and change.change_type == ChangeType.COMMIT

    def test_branch_deletion_ignored(self, monkeypatch):
        monkeypatch.setenv("HARNESS_GITHUB_WEBHOOK_SECRET", SECRET)
        body = json.dumps({"after": "0000000000000000000000000000000000000000",
                           "repository": {"full_name": "org/repo"}}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert ADAPTER.parse_webhook({"x-hub-signature-256": sig, "x-github-event": "push"}, body) is None


class TestDiffFetchUrl:
    async def test_pr_diff_url_and_accept_header(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["accept"] = request.headers["Accept"]
            return httpx.Response(200, text="diff --git a/x b/x")

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="org/repo#12@a",
            provider=GitProvider.GITHUB,
            repo_id="org/repo",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="a",
            base_sha="b",
            pr_number=12,
        )
        diff = await ADAPTER.fetch_diff(change)
        assert diff.startswith("diff --git")
        assert captured["url"].endswith("/repos/org/repo/pulls/12")
        assert captured["accept"] == "application/vnd.github.v3.diff"

    async def test_commit_diff_url(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, text="diff --git a/x b/x")

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="org/repo@abc",
            provider=GitProvider.GITHUB,
            repo_id="org/repo",
            change_type=ChangeType.COMMIT,
            head_sha="abc123",
        )
        await ADAPTER.fetch_diff(change)
        assert captured["url"].endswith("/repos/org/repo/commits/abc123")

    async def test_http_failure_becomes_typed_exception(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, request=request, text="boom")

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="org/repo@abc",
            provider=GitProvider.GITHUB,
            repo_id="org/repo",
            change_type=ChangeType.COMMIT,
            head_sha="abc123",
        )
        with pytest.raises(ProviderHttpError, match="github:fetch_diff"):
            await ADAPTER.fetch_diff(change)


class TestAnnotationPosting:
    async def test_post_annotations_payload_shape(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
        calls: list[tuple[str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((str(request.method), str(request.url), request.content.decode()))
            return httpx.Response(201, request=request, json={"id": 1})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="org/repo#12@a",
            provider=GitProvider.GITHUB,
            repo_id="org/repo",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="a",
            base_sha="b",
            pr_number=12,
        )
        finding = {
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
        report = AnnotationReport(
            gate=GateVerdict(
                status="block",
                blocking_findings=1,
                risk_floor=RiskLevel.HIGH,
                risk_level=RiskLevel.HIGH,
                reason="1 blocking finding(s)",
            ),
            run_status=RunStatus.DEGRADED,
            coverage="partial_explicit",
            lens_coverage="lenses: 1 declared, 1 reported, 0 unavailable",
            not_flagged=("Does not validate runtime config.",),
            degraded_reasons=("budget_exhausted",),
        )
        from harness.models import Finding

        await ADAPTER.post_annotations(change, [Finding.model_validate(finding)], report)
        method, url, body = calls[0]
        assert method == "POST"
        assert url.endswith("/repos/org/repo/pulls/12/comments")
        assert '"line":4' in body
        assert "Gate verdict" in body

    async def test_post_annotations_failure_is_typed(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, request=request, text="nope")

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="org/repo#12@a",
            provider=GitProvider.GITHUB,
            repo_id="org/repo",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="a",
            base_sha="b",
            pr_number=12,
        )
        finding = {
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
        from harness.models import Finding

        with pytest.raises(ProviderHttpError, match="github:post_annotations"):
            await ADAPTER.post_annotations(change, [Finding.model_validate(finding)], None)

    async def test_upsert_summary_comment_edits_existing_marker_comment(self, monkeypatch):
        monkeypatch.delenv("HARNESS_GITHUB_TOKEN", raising=False)
        calls: list[tuple[str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, str(request.url), request.content.decode()))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    request=request,
                    json=[{"id": 99, "body": "<!-- harness-review-summary -->\nold"}],
                )
            return httpx.Response(200, request=request, json={"id": 99})

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched(self, *a, **k):
            k["transport"] = transport
            orig_init(self, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        change = Change(
            id="org/repo#12@a",
            provider=GitProvider.GITHUB,
            repo_id="org/repo",
            change_type=ChangeType.PULL_REQUEST,
            head_sha="a",
            base_sha="b",
            pr_number=12,
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
            not_flagged=("Does not validate runtime config.",),
        )
        await ADAPTER.upsert_summary_comment(change, [], report)
        assert calls[0][0] == "GET"
        assert calls[1][0] == "PATCH"
        assert calls[1][1].endswith("/repos/org/repo/issues/comments/99")
        assert "harness-review-summary" in calls[1][2]
