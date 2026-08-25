"""T009: canonical event contract (FR-021/022)."""

from harness.events import SCHEMA_VERSION, CanonicalEvent, EventType, export_json_schema


class TestEnvelope:
    def test_round_trip(self):
        ev = CanonicalEvent(
            id="evt-1",
            event_type=EventType.PULL_REQUEST_CHANGED,
            source="controlplane.webhooks",
            subject="repos/org/repo/pulls/12",
            data={"head_sha": "abc"},
        )
        parsed = CanonicalEvent.model_validate_json(ev.model_dump_json())
        assert parsed == ev
        assert parsed.schema_version == SCHEMA_VERSION

    def test_all_ten_event_types_defined(self):
        required = {
            "PullRequestChanged", "CommitCreated", "BuildCompleted", "TestCompleted",
            "DeploymentCompleted", "FindingCreated", "FindingResolved",
            "RuntimeSignalObserved", "IncidentOpened", "SpecificationChanged",
        }
        assert {e.value for e in EventType} == required

    def test_json_schema_export(self):
        schema = export_json_schema()
        assert "properties" in schema
        description = str(schema["description"])
        assert description.startswith("Canonical engineering event envelope v1")
