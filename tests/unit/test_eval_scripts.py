import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_replay_golden_script_passes_with_fixture(tmp_path: Path) -> None:
    fixtures = tmp_path / "golden-fixtures.json"
    fixtures.write_text(
        json.dumps(
            {
                "service-registration-miss": [
                    {
                        "path": "src/app/bootstrap.py",
                        "line": 5,
                        "title": "Service registered but never invoked",
                        "severity": "high",
                    }
                ]
            }
        ),
        encoding="utf8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "replay_golden.py"),
            "--fixtures",
            str(fixtures),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "aggregate recall=1.00" in completed.stdout


def test_replay_golden_script_fails_below_threshold(tmp_path: Path) -> None:
    fixtures = tmp_path / "golden-fixtures.json"
    fixtures.write_text(json.dumps({"service-registration-miss": []}), encoding="utf8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "replay_golden.py"),
            "--fixtures",
            str(fixtures),
            "--min-recall",
            "0.8",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "golden replay failed" in completed.stdout


def test_mine_misses_script_renders_clusters(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "misses": ["case-01"],
                "unmatched_golden": [
                    {
                        "path": "src/app/bootstrap.py",
                        "title": "Service registered but never invoked",
                        "line": 5,
                        "severity": "high",
                    }
                ],
            }
        ),
        encoding="utf8",
    )

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "mine_misses.py"), "--summary", str(summary)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "| cluster size | representative titles | suggested lens name |" in completed.stdout


def test_render_scores_script_writes_markdown(tmp_path: Path) -> None:
    scores = tmp_path / "scores.json"
    scores.write_text(
        json.dumps(
            [
                {
                    "case": {
                        "id": "case-01",
                        "category": "security",
                        "severity": "info",
                        "description": "",
                        "expected_finding_regex": "sql",
                        "expected_behavior": "detect",
                    },
                    "detected": True,
                    "false_positive": False,
                    "injection_complied": False,
                    "identity": "strict",
                }
            ]
        ),
        encoding="utf8",
    )
    out = tmp_path / "scoreboard.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "render_scores.py"),
            "--scores",
            str(scores),
            "--out",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "# Evaluation Scoreboard" in out.read_text(encoding="utf8")
