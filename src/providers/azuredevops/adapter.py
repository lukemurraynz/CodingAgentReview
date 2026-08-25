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
from dataclasses import dataclass

import httpx

from harness.models import Change, ChangeType, Finding, GitProvider

from ..base import AnnotationReport, ProviderAdapter, ProviderHttpError
from ..formatting import SUMMARY_MARKER, build_comment_body

_API = "https://dev.azure.com"
_CLOSED_WORKITEM_STATES = {"closed", "done", "resolved"}


@dataclass(frozen=True, slots=True)
class WorkItemSummary:
    id: int
    title: str
    state: str


@dataclass(frozen=True, slots=True)
class LinkedWorkItemCompleteness:
    linked_items: tuple[WorkItemSummary, ...]
    complete: bool | None
    open_states: tuple[str, ...] = ()


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
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, params=params, auth=auth)
                resp.raise_for_status()
                data: dict[str, object] = resp.json()
                raw_changes = data.get("changeDiffs")
                changes = [c for c in raw_changes if isinstance(c, dict)] if isinstance(raw_changes, list) else []
                parts = [str(c.get("diff", {}).get("content", "")) for c in changes]
                return "\n".join(p for p in parts if p)
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

    async def fetch_linked_workitems(self, change: Change) -> LinkedWorkItemCompleteness:
        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return LinkedWorkItemCompleteness((), None)

        org, project, repo = self._split_repo(change.repo_id)
        list_url = (
            f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/"
            f"pullrequests/{change.pr_number}/workitems?api-version=7.1"
        )
        batch_url = f"{_API}/{org}/{project}/_apis/wit/workitemsbatch?api-version=7.1"
        auth = httpx.USE_CLIENT_DEFAULT if not _pat() else ("", _pat())
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                linked = await client.get(list_url, auth=auth)
                linked.raise_for_status()
                workitem_ids = _parse_linked_workitem_ids(linked.json())
                if not workitem_ids:
                    return LinkedWorkItemCompleteness((), None)

                batch = await client.post(
                    batch_url,
                    json={
                        "ids": workitem_ids,
                        "fields": ["System.Id", "System.Title", "System.State"],
                    },
                    auth=auth,
                )
                batch.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderHttpError(
                self.name,
                "fetch_linked_workitems",
                url=str(exc.request.url),
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            url = batch_url if "workitemsbatch" in str(exc) else list_url
            raise ProviderHttpError(self.name, "fetch_linked_workitems", url=url, detail=str(exc)) from exc

        summaries = _parse_workitem_summaries(batch.json())
        by_id = {item.id: item for item in summaries}
        ordered = tuple(by_id[item_id] for item_id in workitem_ids if item_id in by_id)
        open_states = tuple(item.state for item in ordered if item.state.strip().lower() not in _CLOSED_WORKITEM_STATES)
        return LinkedWorkItemCompleteness(ordered, not open_states, open_states)

    async def post_annotations(
        self,
        change: Change,
        findings: list[Finding],
        report: AnnotationReport | None = None,
    ) -> None:
        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return
        if not findings:
            return
        org, project, repo = self._split_repo(change.repo_id)
        url = (
            f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/"
            f"pullrequests/{change.pr_number}/threads?api-version=7.1"
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                auth = ("", _pat()) if _pat() else httpx.USE_CLIENT_DEFAULT
                for finding in findings:
                    evidence = finding.evidence[0]
                    body = {
                        "comments": [
                            {
                                "parentCommentId": 0,
                                "content": build_comment_body([finding], report, include_marker=False),
                                "commentType": 1,
                            }
                        ],
                        "status": 1,
                        "threadContext": {
                            "filePath": f"/{evidence.path}",
                            "rightFileStart": {"line": evidence.line_start or 1, "offset": 1},
                            "rightFileEnd": {"line": evidence.line_end or evidence.line_start or 1, "offset": 1},
                        },
                    }
                    resp = await client.post(url, json=body, auth=auth)
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
            raise ProviderHttpError(self.name, "post_annotations", url=url, detail=str(exc)) from exc

    async def upsert_summary_comment(
        self,
        change: Change,
        findings: list[Finding],
        report: AnnotationReport | None = None,
    ) -> None:
        if change.change_type != ChangeType.PULL_REQUEST or change.pr_number is None:
            return
        org, project, repo = self._split_repo(change.repo_id)
        threads_url = (
            f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/"
            f"pullrequests/{change.pr_number}/threads?api-version=7.1"
        )
        body_text = build_comment_body(findings, report)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                auth = ("", _pat()) if _pat() else httpx.USE_CLIENT_DEFAULT
                existing = await client.get(threads_url, auth=auth)
                existing.raise_for_status()
                thread_id = _find_summary_thread_id(existing.json())
                if thread_id is None:
                    payload = {
                        "comments": [{"parentCommentId": 0, "content": body_text, "commentType": 1}],
                        "status": 1,
                    }
                    resp = await client.post(threads_url, json=payload, auth=auth)
                else:
                    resp = await client.patch(
                        f"{_API}/{org}/{project}/_apis/git/repositories/{repo}/"
                        f"pullrequests/{change.pr_number}/threads/{thread_id}/comments/1?api-version=7.1",
                        json={"content": body_text},
                        auth=auth,
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
            raise ProviderHttpError(self.name, "upsert_summary_comment", url=threads_url, detail=str(exc)) from exc

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


def _find_summary_thread_id(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("value")
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        comments = item.get("comments")
        if not isinstance(comments, list) or not comments:
            continue
        first = comments[0]
        if not isinstance(first, dict):
            continue
        content = first.get("content")
        if isinstance(content, str) and SUMMARY_MARKER in content:
            thread_id = item.get("id")
            if isinstance(thread_id, int):
                return thread_id
    return None


def _parse_linked_workitem_ids(payload: object) -> list[int]:
    if not isinstance(payload, dict):
        return []
    value = payload.get("value")
    if not isinstance(value, list):
        return []
    ids: list[int] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, int):
            ids.append(raw_id)
            continue
        if isinstance(raw_id, str) and raw_id.isdigit():
            ids.append(int(raw_id))
    return ids


def _parse_workitem_summaries(payload: object) -> tuple[WorkItemSummary, ...]:
    if not isinstance(payload, dict):
        return ()
    value = payload.get("value")
    if not isinstance(value, list):
        return ()
    summaries: list[WorkItemSummary] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields")
        if not isinstance(fields, dict):
            continue
        item_id = item.get("id")
        title = fields.get("System.Title")
        state = fields.get("System.State")
        if not isinstance(item_id, int) or not isinstance(title, str) or not isinstance(state, str):
            continue
        summaries.append(WorkItemSummary(id=item_id, title=title, state=state))
    return tuple(summaries)
