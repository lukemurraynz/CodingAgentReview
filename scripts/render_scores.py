"""Render category and identity scoreboards from persisted score records."""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pydantic>=2.9",
# ]
# ///
# ─── How to run ─────────────────────────────────────────────────────────────
# uv run python scripts/render_scores.py --scores benchmark/scores.json --out benchmark/scoreboard.md

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eval.score import load_scores, score_by_category, summarize


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    scores = load_scores(json.loads(args.scores.read_text(encoding="utf8")))
    overall = summarize(scores)
    categories = score_by_category(scores)

    lines = [
        "# Evaluation Scoreboard",
        "",
        "## Overall",
        "",
        overall.to_markdown(),
        "",
        "## Per Category",
        "",
        "| category | detection rate | false positives | injections complied |",
        "|---|---|---|---|",
    ]
    for category, summary in categories.items():
        lines.append(
            f"| {category} | {summary.detection_rate} | {summary.false_positives} | "
            f"{summary.injections_complied}/{summary.injections_total} |"
        )

    if overall.identities:
        lines.extend(
            [
                "",
                "## Per Identity",
                "",
                "| identity | detection rate | false positives | injections complied |",
                "|---|---|---|---|",
            ]
        )
        for identity, summary in overall.identities.items():
            lines.append(
                f"| {identity} | {summary.detection_rate} | {summary.false_positives} | "
                f"{summary.injections_complied}/{summary.injections_total} |"
            )

    output = "\n".join(lines) + "\n"
    if args.out is None:
        print(output, end="")
        return 0
    args.out.write_text(output, encoding="utf8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
