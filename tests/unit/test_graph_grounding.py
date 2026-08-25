"""T072: risk drivers must cite applicable specification evidence."""

from pathlib import Path

from graph import (
    ground_risk_drivers,
    load_specifications,
    merge_specifications,
    resolve_applicable_specifications,
)
from graph.scanner import discover_specification_artifacts
from harness.models import RiskLevel, RiskSignal


def test_grounded_risk_drivers_reference_applicable_specs(tmp_path: Path):
    spec_dir = tmp_path / "specs" / "security"
    spec_dir.mkdir(parents=True)
    (spec_dir / "auth.spec.json").write_text(
        '{"id": "auth-boundary", "title": "Auth", "applies_to": ["identity"], '
        '"invariants": [{"id": "auth-tests", "applies_to": ["identity"]}]}',
        encoding="utf-8",
    )
    graph = merge_specifications(load_specifications(discover_specification_artifacts(tmp_path)))
    applicable = resolve_applicable_specifications(
        graph,
        changed_paths=["src/auth/permissions.py"],
        resource_types=["identity"],
    )

    grounded = ground_risk_drivers(
        applicable,
        [RiskSignal(name="identity_boundary_changed", level=RiskLevel.HIGH, detail="auth code changed")],
    )

    assert [driver.signal_name for driver in grounded] == ["identity_boundary_changed"]
    assert grounded[0].evidence == ("auth-boundary@specs/security/auth.spec.json#0[auth-tests]",)
