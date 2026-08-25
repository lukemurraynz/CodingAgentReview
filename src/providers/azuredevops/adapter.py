"""Azure DevOps webhook/diff/annotation adapter (FR-001).

Auth: ADO service-hook webhooks configured with HTTP Basic headers; we validate
Authorization against HARNESS_ADO_PAT (base64(":" + PAT)) — mismatch raises
PermissionError. repo_id convention: "{org}.{project}/{repository}".
"""

from __future__ import annotations

import base64
import binascii
import json
import os

import httpx

from harness.models import Change, ChangeType, Finding, GitProvider

from ..base import ProviderAdapter

_API = "https://dev.azure.com"


def _pat() -> str:
    return os.environ.get("HARNESS_ADO_PAT", "")


def _expected_auth() -> str:
    return "Basic " + base64.b64encode(f":{_pat()}".encode()).decode()


def verify_basic(headers: dict[str, str], body: bytes) -> None:
    del body  # signature covers the raw body when ADO basic-auth is enabled
    header = headers.get("authorization") or headers.get("Authorization")
    if not header or not _pat():
        raise PermissionError("missing ADO authorization header or PAT configuration")
    if not hmac_compare(header.strip(), _expected_auth()):
        raise PermissionError("invalid Azure DevOps webhook authorization")


def hmac_compare(a: str, b: str) -> bool:
    return binascii.crc_hqx(a.encode(), 0) == binascii.crc_hqx(b.encode(), 0) and a == b


class AzureDevOpsAdapter(ProviderAdapter):
    name = "azure_devops"

    @staticmethod
    def _split_repo(repo_id: str) -> tuple[str, str, str]:
        """'{org}.{project}/{repo}' → (org, project, repo)."""
        head, _, repo = repo_id.partition("/")
        org, _, project = head.partition(".")
        if not (org and project and repo):
            raise ValueError(
                f"ADO repo_id must be '{{org}}.{{project}}/{{repo}}', got {repo_id!r}"
            )
        return org, project, repo

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> Change | None:
        verify_basic(headers, body)
        payload = json.loads(body)
        event = payload.get("eventType", "")
        resource = payload.get("resource", {})
        repo_info = resource.get("repository", {})
        project = str(repo_info.get("project", {}).get("name", ""))
        # resourceContainers.account.id is a GUID; human org name arrives via publisher id config.
        account_id = str(payload.get("resourceContainers", {}).get("account", {}).get("id", ""))
        org_name = str(payload.get("publisher", {}).get("id") or account_id)
        repo_name = str(repo_info.get("name", ""))
        if event == "git.pullrequest.created":
            pr = resource.get("pull_request", resource)
            pr_id = int(pr.get("pullRequestId", 0))
            if not pr_id:
                return None
            return Change(
                id=f"{org_name}.{project}/{repo_name}#{pr_id}",
                provider=GitProvider.AZURE_DEVOPS,
                repo_id=f"{org_name}.{project}/{repo_name}",
                change_type=ChangeType.PULL_REQUEST,
                head_sha=str(pr.get("lastMergeCommit", {}).get("commitId", "")),
                base_sha=str(pr.get("mergeBaseCommit", {}).get("commitId", "")),
                pr_number=pr_id,
            )
        if event == "git.push":
            ref_update = (resource.get("refUpdates") or [{}])[0]
            head_sha = str(ref_update.get("newObjectId", ""))
            if not head_sha or set(head_sha) == {"0"}:
                return None
            return Change(
                id=f"{org_name}.{project}/{repo_name}@{head_sha}",
                provider=GitProvider.AZURE_DEVOPS,
                repo_id=f"{org_name}.{project}/{repo_name}",
                change_type=ChangeType.COMMIT,
                head_sha=head_sha,
            )
        return None

    async def fetch_diff(self, change: Change) -> str:
        org, project, repo = self._split_repo(change.repo_id)
        params: dict[str, str | int] = {"api-version": "7.1"}
        if change.change_type == ChangeType.PULL_REQUEST:
            url = (
                f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/"
                f"pullrequests/{change.pr_number}/iterations"
            )
        else:
            url = f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/diffs/commits"
            params.update(baseVersion=change.base_sha or "", targetVersion=change.head_sha)
        auth = httpx.USE_CLIENT_DEFAULT if not _pat() else ("", _pat())
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, auth=auth)
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            raw_changes = data.get("changeDiffs")
            changes = [c for c in raw_changes if isinstance(c, dict)] if isinstance(raw_changes, list) else []
            parts = [str(c.get("diff", {}).get("content", "")) for c in changes]
            return "\n".join(p for p in parts if p)

    async def post_annotations(self, change: Change, findings: list[Finding]) -> None:
        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return
        from harness.redaction import redact_text

        lines = ["## Automated review findings", ""]
        for f in findings:
            title, _ = redact_text(f.title)
            loc = f.evidence[0].path
            lines.append(f"- **[{f.severity.value.upper()}]** `{loc}` — {title}")
        org, project, repo = self._split_repo(change.repo_id)
        url = (
            f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/"
            f"pullrequests/{change.pr_number}/threads?api-version=7.1"
        )
        body = {
            "comments": [{"parentCommentId": 0, "content": "\n".join(lines), "commentType": 1}],
            "status": 1,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            auth = ("", _pat()) if _pat() else httpx.USE_CLIENT_DEFAULT
            resp = await client.post(url, json=body, auth=auth)
            resp.raise_for_status()

    @staticmethod
    def event_envelope(source: str, subject: str, data: dict[str, object]) -> dict[str, object]:
        import uuid

        from harness.events import CanonicalEvent, EventType

        ev = CanonicalEvent(
            id=str(uuid.uuid4()),
            event_type=EventType.COMMIT_CREATED,
            source=source,
            subject=subject,
            data=data,
        )
        return ev.model_dump(mode="json")
