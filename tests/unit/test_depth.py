"""Risk-depth policy matrix."""

from harness.models import ChangeClassification, RiskLevel, RiskSignal
from worker.depth import classify_review_depth, coerce_risk_signals


def test_docs_only_stays_deterministic():
    policy = classify_review_depth(
        classification=ChangeClassification.DOCS,
        changed_paths=["README.md", "docs/guide.rst"],
    )
    assert policy.risk_floor == RiskLevel.LOW
    assert policy.risk_level == RiskLevel.LOW
    assert policy.lenses == ("structural", "production_validation")


def test_lockfile_only_stays_deterministic():
    policy = classify_review_depth(
        classification=ChangeClassification.CODE,
        changed_paths=["uv.lock", "Cargo.lock"],
    )
    assert policy.risk_floor == RiskLevel.LOW
    assert policy.risk_level == RiskLevel.LOW
    assert policy.lenses == ("structural", "production_validation")


def test_identity_path_escalates_security():
    policy = classify_review_depth(
        classification=ChangeClassification.CODE,
        changed_paths=["src/auth/permissions.py"],
    )
    assert policy.risk_floor == RiskLevel.HIGH
    assert policy.risk_level == RiskLevel.HIGH
    assert policy.lenses[-1] == "security"


def test_production_infra_path_escalates_security():
    policy = classify_review_depth(
        classification=ChangeClassification.CODE,
        changed_paths=["infra/main.bicep"],
    )
    assert policy.risk_floor == RiskLevel.HIGH
    assert policy.risk_level == RiskLevel.HIGH
    assert policy.lenses == ("structural", "production_validation", "correctness", "security")


def test_explicit_medium_signal_keeps_standard_set():
    policy = classify_review_depth(
        classification=ChangeClassification.CODE,
        changed_paths=["src/app.py"],
        risk_signals=[RiskSignal(name="test_delta", level=RiskLevel.MEDIUM)],
    )
    assert policy.risk_floor == RiskLevel.MEDIUM
    assert policy.risk_level == RiskLevel.MEDIUM
    assert policy.lenses == ("structural", "production_validation", "correctness")


def test_critical_signal_requires_acknowledgement():
    policy = classify_review_depth(
        classification=ChangeClassification.CODE,
        changed_paths=["src/app.py"],
        risk_signals=[RiskSignal(name="prod_target", level=RiskLevel.CRITICAL)],
    )
    assert policy.risk_floor == RiskLevel.MEDIUM
    assert policy.risk_level == RiskLevel.CRITICAL
    assert policy.acknowledgement_required is True
    assert policy.lenses[-1] == "security"


def test_monotonic_risk_floor_never_allows_downstream_downgrade():
    policy = classify_review_depth(
        classification=ChangeClassification.CODE,
        changed_paths=["src/auth/policy.py"],
        risk_signals=[RiskSignal(name="lens_lowball", level=RiskLevel.LOW)],
    )
    assert policy.risk_floor == RiskLevel.HIGH
    assert policy.risk_level == RiskLevel.HIGH


def test_downstream_signal_can_raise_but_not_lower_diff_floor():
    policy = classify_review_depth(
        classification=ChangeClassification.DOCS,
        changed_paths=["README.md"],
        risk_signals=[RiskSignal(name="prod_target", level=RiskLevel.CRITICAL)],
    )
    assert policy.risk_floor == RiskLevel.LOW
    assert policy.risk_level == RiskLevel.CRITICAL
    assert policy.acknowledgement_required is True


def test_dict_signals_coerce():
    signals = coerce_risk_signals([{"name": "boundary", "level": "high", "detail": "x"}])
    assert len(signals) == 1
    assert signals[0].level == RiskLevel.HIGH
