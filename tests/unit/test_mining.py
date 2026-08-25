from pathlib import Path

from eval.golden import GoldenFinding
from eval.mining import (
    MiningSignal,
    cluster_signals,
    render_cluster_markdown,
    signals_from_case_misses,
    signals_from_unmatched_golden,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_signals_from_case_misses_resolve_paths_and_categories() -> None:
    signals = signals_from_case_misses(["case-01"], REPO_ROOT / "benchmark" / "cases")

    assert {signal.category for signal in signals} == {"correctness", "production_validation", "security", "structural"}
    assert any(signal.path.endswith("benchmark/cases/production_validation/case-01/defect.py") for signal in signals)


def test_signals_from_unmatched_golden_maps_findings() -> None:
    signals = signals_from_unmatched_golden(
        [GoldenFinding(path="src/a.py", title="Service registered but never invoked", severity="high")]
    )

    assert signals == [MiningSignal(path="src/a.py", title="Service registered but never invoked")]


def test_cluster_signals_is_deterministic() -> None:
    signals = [
        MiningSignal(path="src/app/bootstrap.py", title="Service registered but never invoked"),
        MiningSignal(path="src/app/bootstrap.py", title="Legacy exporter registered but never called"),
        MiningSignal(path="src/security/sql.py", title="SQL built via f-string interpolation"),
    ]

    first = cluster_signals(list(reversed(signals)))
    second = cluster_signals(signals)

    assert first == second
    assert first[0].cluster_size == 2
    assert first[0].suggested_lens == "registered_followup"


def test_render_cluster_markdown_includes_table_rows() -> None:
    clusters = cluster_signals([MiningSignal(path="src/a.py", title="Service registered but never invoked")])

    markdown = render_cluster_markdown(clusters)

    assert "| cluster size | representative titles | suggested lens name |" in markdown
    assert "registered_followup" in markdown
