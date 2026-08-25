from eval.score import (
    SC004_MAX_FP_RATE,
    SC004_MIN_DETECTION,
    CaseDef,
    CaseScore,
    dump_scores,
    load_scores,
    sc004_gate_failures_identities,
    score_by_category,
    summarize,
)


def _case(case_id: str, category: str, *, behavior: str = "detect", regex: str = "match") -> CaseDef:
    return CaseDef(id=case_id, category=category, expected_behavior=behavior, expected_finding_regex=regex)


def test_score_by_category_groups_scores() -> None:
    scores = [
        CaseScore(case=_case("a", "security"), detected=True),
        CaseScore(case=_case("b", "security"), detected=False),
        CaseScore(case=_case("c", "clean"), false_positive=True),
    ]

    grouped = score_by_category(scores)

    assert grouped["security"].detected_count == 1
    assert grouped["security"].misses == ["b"]
    assert grouped["clean"].false_positives == 1


def test_summarize_groups_by_identity() -> None:
    scores = [
        CaseScore(case=_case("a", "security"), detected=True, identity="strict"),
        CaseScore(case=_case("b", "security"), detected=False, identity="strict"),
        CaseScore(case=_case("c", "security"), detected=True, identity="balanced"),
    ]

    summary = summarize(scores)

    assert set(summary.identities) == {"balanced", "strict"}
    assert summary.identities["strict"].misses == ["b"]
    assert summary.identities["balanced"].detected_count == 1


def test_identity_gate_reports_failing_identity_only() -> None:
    passing = summarize(
        [
            CaseScore(case=_case("a", "security"), detected=True, identity="strict"),
            CaseScore(case=_case("clean-a", "clean", regex=""), identity="strict"),
        ]
    )
    failing = summarize(
        [
            CaseScore(case=_case("b", "security"), detected=False, identity="balanced"),
            CaseScore(case=_case("clean-b", "clean", regex=""), false_positive=True, identity="balanced"),
        ]
    )

    failures = sc004_gate_failures_identities({"strict": passing, "balanced": failing})

    assert any(failure.startswith("identity balanced: detection rate") for failure in failures)
    assert any(failure.startswith("identity balanced: false-positive rate") for failure in failures)
    assert not any(failure.startswith("identity strict") for failure in failures)


def test_identity_gate_requires_summaries() -> None:
    assert sc004_gate_failures_identities({}) == ["no identity summaries scored"]


def test_dump_and_load_scores_round_trip_identity() -> None:
    scores = [CaseScore(case=_case("a", "security"), detected=True, identity="strict")]

    loaded = load_scores(dump_scores(scores))

    assert loaded == scores


def test_identity_matrix_matches_sc004_thresholds() -> None:
    assert SC004_MIN_DETECTION == 0.80
    assert SC004_MAX_FP_RATE == 0.15
