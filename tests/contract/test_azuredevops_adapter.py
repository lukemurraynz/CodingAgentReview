"""Contract tests: Azure DevOps adapter auth/parsing."""

import base64
import json

import pytest

from harness.models import ChangeType
from providers.azuredevops import AzureDevOpsAdapter

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
