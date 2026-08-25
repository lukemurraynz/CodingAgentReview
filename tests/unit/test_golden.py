from pathlib import Path

import pytest

from eval.golden import GoldenFinding, load_golden, replay_golden, score_against_expected

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_score_against_expected_matches_and_tracks_unmatched() -> None:
    findings = [
        GoldenFinding(
            path="src/app/bootstrap.py",
            line=5,
            title="Service registered but never invoked",
            severity="high",
        ),
        GoldenFinding(path="src/other.py", line=4, title="Noise finding", severity="low"),
    ]
    expected = load_golden(REPO_ROOT / "benchmark" / "golden")[0].expected

    score = score_against_expected(findings, expected)

    assert score.recall == 1.0
    assert len(score.matched) == 1
    assert len(score.unmatched) == 1
    assert score.missed == []


def test_score_against_expected_tracks_misses() -> None:
    expected = load_golden(REPO_ROOT / "benchmark" / "golden")[0].expected

    score = score_against_expected([], expected)

    assert score.recall == 0.0
    assert len(score.missed) == 1


def test_load_golden_requires_both_files(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-a"
    case_dir.mkdir()
    (case_dir / "diff.patch").write_text("diff --git a/x b/x\n", encoding="utf8")

    with pytest.raises(ValueError, match="missing required files"):
        load_golden(tmp_path)


def test_replay_golden_aggregates_recall() -> None:
    cases = load_golden(REPO_ROOT / "benchmark" / "golden")

    replay = replay_golden(
        cases,
        lambda _case: [
            GoldenFinding(
                path="src/app/bootstrap.py",
                line=5,
                title="Service registered but never invoked",
                severity="high",
            )
        ],
    )

    assert replay.recall == 1.0
    assert replay.matched_count == 1
    assert replay.missed_count == 0
