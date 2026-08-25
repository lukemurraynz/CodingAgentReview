"""Replay harvested golden diffs against a findings source and gate on recall."""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pydantic>=2.9",
# ]
# ///
# ─── How to run ─────────────────────────────────────────────────────────────
# uv run python scripts/replay_golden.py --fixtures benchmark/golden-fixtures.json

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eval.golden import GoldenCase, GoldenFinding, load_findings_payload, load_golden, replay_golden


class FixtureFindingsSource:
    """Offline findings source keyed by golden case id."""

    def __init__(self, fixture_path: Path) -> None:
        payload = json.loads(fixture_path.read_text(encoding="utf8"))
        if not isinstance(payload, dict):
            raise ValueError("fixtures payload must be an object keyed by golden case id")
        self._payload = payload

    def load(self, case: GoldenCase) -> list[GoldenFinding]:
        try:
            case_payload = self._payload[case.id]
        except KeyError as exc:
            raise KeyError(f"missing fixture findings for golden case {case.id}") from exc
        return load_findings_payload(json.dumps(case_payload))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("benchmark") / "golden")
    parser.add_argument("--fixtures", type=Path, required=True, help="JSON file keyed by golden case id")
    parser.add_argument("--min-recall", type=float, default=0.8)
    args = parser.parse_args()

    source = FixtureFindingsSource(args.fixtures)
    cases = load_golden(args.root)
    replay = replay_golden(cases, source.load)
    for case_result in replay.cases:
        case = case_result.case
        recall = case_result.score
        print(
            f"{case.id}: recall={recall.recall:.2f} matched={len(recall.matched)} "
            f"missed={len(recall.missed)} unmatched={len(recall.unmatched)}"
        )
    print(
        "\n"
        f"aggregate recall={replay.recall:.2f} matched={replay.matched_count} "
        f"missed={replay.missed_count} unmatched={replay.unmatched_count}"
    )
    if replay.recall < args.min_recall:
        print(f"golden replay failed: recall {replay.recall:.2f} < {args.min_recall:.2f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
