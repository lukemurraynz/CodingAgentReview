from pathlib import Path

from eval.score import CaseDef, load_cases, score_review

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_registered_but_never_called_case_is_flagged_as_production_validation_detection() -> None:
    cases = {
        case.id: case
        for case, _ in load_cases(REPO_ROOT / "benchmark" / "cases")
        if case.category == "production_validation"
    }
    case = cases["case-01"]

    scored = score_review(case, "Service registered but never called; defined but not wired into any execution path.")

    assert isinstance(case, CaseDef)
    assert case.category == "production_validation"
    assert scored.detected is True
    assert scored.kind == "detected"
