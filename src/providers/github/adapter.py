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

from ..base import ProviderAdapter

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
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text

    async def post_annotations(self, change: Change, findings: list[Finding]) -> None:
        from harness.redaction import redact_text

        lines = ["## Automated review findings", ""]
        by_severity: dict[str, list[Finding]] = {}
        for f in findings:
            by_severity.setdefault(f.severity.value, []).append(f)
        for sev in ("blocker", "high", "medium", "low", "info"):
            for f in by_severity.get(sev, []):
                loc = f.evidence[0].path
                if f.evidence[0].line_start:
                    loc += f":{f.evidence[0].line_start}"
                title, _ = redact_text(f.title)
                detail, _ = redact_text(f.detail or "")
                lines.append(f"- **[{sev.upper()}]** `{loc}` — {title}")
                if detail:
                    lines.append(f"  {detail}")
        if len(lines) == 2:
            lines.append("No blocking findings.")

        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return  # commit reviews are recorded in state only, no PR thread to post to
        token = _token()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{_API}/repos/{change.repo_id}/issues/{change.pr_number}/comments"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json={"body": "\n".join(lines)})
            resp.raise_for_status()

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
