"""Cluster SC-004 misses and unmatched golden findings into lens candidates."""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pydantic>=2.9",
#   "pyyaml>=6.0.3",
# ]
# ///
# ─── How to run ─────────────────────────────────────────────────────────────
# uv run python scripts/mine_misses.py --summary benchmark/miss-summary.json

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eval.golden import load_findings_payload
from eval.mining import (
    cluster_signals,
    render_cluster_markdown,
    signals_from_case_misses,
    signals_from_unmatched_golden,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--benchmark-root", type=Path, default=Path("benchmark") / "cases")
    args = parser.parse_args()

    payload = json.loads(args.summary.read_text(encoding="utf8"))
    if not isinstance(payload, dict):
        raise ValueError("summary payload must be an object")
    misses = payload.get("misses", [])
    unmatched = payload.get("unmatched_golden", [])
    if not isinstance(misses, list) or not isinstance(unmatched, list):
        raise ValueError("summary payload must include list fields: misses, unmatched_golden")

    signals = signals_from_case_misses(misses, args.benchmark_root)
    signals.extend(signals_from_unmatched_golden(load_findings_payload(json.dumps(unmatched))))
    print(render_cluster_markdown(cluster_signals(signals)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
