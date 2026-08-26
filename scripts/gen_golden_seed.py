"""Generate golden-seed fixtures from the offline benchmark corpus."""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pydantic>=2.9",
#   "pyyaml>=6.0.3",
# ]
# ///
# ─── How to run ─────────────────────────────────────────────────────────────
# uv run python scripts/gen_golden_seed.py

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eval.score import CaseDef, load_cases

DEFAULT_BENCHMARK_ROOT = Path("benchmark") / "cases"
DEFAULT_OUTPUT_ROOT = Path("benchmark") / "golden-seeds"
MERGE_OUTPUT_ROOT = Path("benchmark") / "golden"


@dataclass(frozen=True, slots=True)
class GoldenSeedCase:
    """Generated seed artifacts for a single benchmark case."""

    case_id: str
    output_dir_name: str
    diff_text: str
    expected_payload: tuple[dict[str, str | None], ...]


def build_seed_cases(benchmark_root: Path) -> list[GoldenSeedCase]:
    """Convert every benchmark case into a deterministic golden-seed payload."""

    out: list[GoldenSeedCase] = []
    for case, folder in load_cases(benchmark_root):
        source_file = _resolve_source_file(folder)
        relative_path = source_file.relative_to(benchmark_root).as_posix()
        out.append(
            GoldenSeedCase(
                case_id=case.id,
                output_dir_name=f"seed-{case.category}-{case.id}",
                diff_text=_render_diff(relative_path, source_file.read_text(encoding="utf8")),
                expected_payload=_build_expected_payload(case, relative_path),
            )
        )
    return out


def write_seed_cases(cases: list[GoldenSeedCase], output_root: Path) -> None:
    """Persist golden-seed cases idempotently to disk."""

    output_root.mkdir(parents=True, exist_ok=True)
    for case in cases:
        case_root = output_root / case.output_dir_name
        case_root.mkdir(parents=True, exist_ok=True)
        (case_root / "diff.patch").write_text(_normalize_text(case.diff_text), encoding="utf8")
        expected_text = json.dumps(list(case.expected_payload), indent=2) + "\n"
        (case_root / "expected.json").write_text(expected_text, encoding="utf8")


def _resolve_source_file(case_root: Path) -> Path:
    candidates = [case_root / "code.py", case_root / "defect.py"]
    existing = [candidate for candidate in candidates if candidate.exists()]
    if len(existing) != 1:
        raise ValueError(f"expected exactly one source file in {case_root}")
    return existing[0]


def _render_diff(relative_path: str, content: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            [],
            content.splitlines(),
            fromfile="/dev/null",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    return "\n".join([f"diff --git a/{relative_path} b/{relative_path}", "new file mode 100644", *diff_lines])


def _build_expected_payload(case: CaseDef, relative_path: str) -> tuple[dict[str, str | None], ...]:
    if not case.expected_finding_regex:
        return ()
    return (
        {
            "path": relative_path,
            "title_regex": case.expected_finding_regex,
            "severity": None,
        },
    )


def _normalize_text(value: str) -> str:
    return value.rstrip("\n") + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, default=DEFAULT_BENCHMARK_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--merge", action="store_true", help="write into benchmark/golden instead of golden-seeds")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_root = MERGE_OUTPUT_ROOT if args.merge else args.out
    write_seed_cases(build_seed_cases(args.benchmark_root), output_root)
    print(f"wrote golden seeds to {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
