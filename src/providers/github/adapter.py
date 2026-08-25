"""GitHub webhook/diff/annotation adapter (FR-001).

Signature verification: X-Hub-Signature-256 (HMAC-SHA256) against
HARNESS_GITHUB_WEBHOOK_SECRET; mismatch raises PermissionError.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx

from harness.models import Change, ChangeType, Finding, GitProvider

from ..base import AnnotationReport, ProviderAdapter, ProviderHttpError
from ..formatting import SUMMARY_MARKER, build_comment_body

_API = "https://api.github.com"
_DIFF_ACCEPT = "application/vnd.github.v3.diff"


def _secret() -> str:
    return os.environ.get("HARNESS_GITHUB_WEBHOOK_SECRET", "")


def _token() -> str | None:
    return os.environ.get("HARNESS_GITHUB_TOKEN") or None


def verify_signature(headers: dict[str, str], body: bytes) -> None:
    header = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
    if not header or not _secret():
        raise PermissionError("missing GitHub signature or webhook secret")
    expected = "sha256=" + hmac.new(_secret().encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(header, expected):
        raise PermissionError("invalid GitHub webhook signature")


class GitHubAdapter(ProviderAdapter):
    name = "github"

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> Change | None:
        """Translate a GitHub webhook into a Change.

        Returns None for events that do not trigger review. Raises PermissionError
        on signature failure.
        """
        verify_signature(headers, body)

        import json

        payload = json.loads(body)
        event = headers.get("x-github-event") or headers.get("X-GitHub-Event", "")
        repo_full = payload.get("repository", {}).get("full_name", "")
        if not repo_full:
            return None

        if event == "pull_request" and payload.get("action") in ("opened", "synchronize"):
            pr = payload["pull_request"]
            return Change(
                id=f"{repo_full}#{pr['number']}@{pr['head']['sha']}",
                provider=GitProvider.GITHUB,
                repo_id=repo_full,
                change_type=ChangeType.PULL_REQUEST,
                head_sha=pr["head"]["sha"],
                base_sha=pr["base"]["sha"],
                pr_number=int(pr["number"]),
                changed_files=[f.get("filename", "") for f in pr.get("changed_files", [])]
                if isinstance(pr.get("changed_files"), list)
                else [],
            )

        if event == "push":
            after = str(payload.get("after") or "")
            if not after or set(after) == {"0"}:
                return None  # branch deletion
            return Change(
                id=f"{repo_full}@{after}",
                provider=GitProvider.GITHUB,
                repo_id=repo_full,
                change_type=ChangeType.COMMIT,
                head_sha=after,
            )

        return None

    async def fetch_diff(self, change: Change) -> str:
        url = (
            f"{_API}/repos/{change.repo_id}/pulls/{change.pr_number}"
            if change.change_type == ChangeType.PULL_REQUEST
            else f"{_API}/repos/{change.repo_id}/commits/{change.head_sha}"
        )
        headers = {"Accept": _DIFF_ACCEPT}
        token = _token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPStatusError as exc:
            raise ProviderHttpError(
                self.name,
                "fetch_diff",
                url=str(exc.request.url),
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderHttpError(self.name, "fetch_diff", url=url, detail=str(exc)) from exc

    async def post_annotations(
        self,
        change: Change,
        findings: list[Finding],
        report: AnnotationReport | None = None,
    ) -> None:
        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return  # commit reviews are recorded in state only, no PR thread to post to
        if not findings:
            return
        token = _token()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        base_url = f"{_API}/repos/{change.repo_id}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for finding in findings:
                    evidence = finding.evidence[0]
                    if evidence.line_start is None:
                        continue
                    comment_url = f"{base_url}/pulls/{change.pr_number}/comments"
                    body = {
                        "body": build_comment_body([finding], report, include_marker=False),
                        "commit_id": change.head_sha,
                        "path": evidence.path,
                        "line": evidence.line_start,
                        "side": "RIGHT",
                    }
                    resp = await client.post(comment_url, headers=headers, json=body)
                    resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderHttpError(
                self.name,
                "post_annotations",
                url=str(exc.request.url),
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderHttpError(self.name, "post_annotations", url=base_url, detail=str(exc)) from exc

    async def upsert_summary_comment(
        self,
        change: Change,
        findings: list[Finding],
        report: AnnotationReport | None = None,
    ) -> None:
        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return
        token = _token()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        base_url = f"{_API}/repos/{change.repo_id}"
        issues_comments_url = f"{base_url}/issues/{change.pr_number}/comments"
        summary_body = build_comment_body(findings, report)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                existing = await client.get(issues_comments_url, headers=headers)
                existing.raise_for_status()
                comments = existing.json()
                comment_id = _find_existing_comment_id(comments)
                if comment_id is None:
                    resp = await client.post(issues_comments_url, headers=headers, json={"body": summary_body})
                else:
                    resp = await client.patch(
                        f"{base_url}/issues/comments/{comment_id}",
                        headers=headers,
                        json={"body": summary_body},
                    )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderHttpError(
                self.name,
                "upsert_summary_comment",
                url=str(exc.request.url),
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderHttpError(
                self.name,
                "upsert_summary_comment",
                url=issues_comments_url,
                detail=str(exc),
            ) from exc

    @staticmethod
    def event_envelope(source: str, subject: str, data: dict[str, object]) -> dict[str, object]:
        import uuid

        from harness.events import CanonicalEvent, EventType

        ev = CanonicalEvent(
            id=str(uuid.uuid4()),
            event_type=EventType.PULL_REQUEST_CHANGED,
            source=source,
            subject=subject,
            data=data,
        )
        return ev.model_dump(mode="json")


def _find_existing_comment_id(comments: object) -> int | None:
    if not isinstance(comments, list):
        return None
    for item in comments:
        if not isinstance(item, dict):
            continue
        body = item.get("body")
        if isinstance(body, str) and SUMMARY_MARKER in body:
            comment_id = item.get("id")
            if isinstance(comment_id, int):
                return comment_id
    return None
