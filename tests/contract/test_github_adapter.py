"""Contract tests: GitHub adapter signature/parsing/diff URL construction."""

import hashlib
import hmac
import json

import httpx
import pytest

from harness.models import Change, ChangeType, GitProvider
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
