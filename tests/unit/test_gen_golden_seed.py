from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parent.parent.parent
    module_path = repo_root / "scripts" / "gen_golden_seed.py"
    spec = importlib.util.spec_from_file_location("gen_golden_seed", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_case(root: Path, *, category: str, case_id: str, yaml_text: str, file_name: str, code: str) -> None:
    case_root = root / category / case_id
    case_root.mkdir(parents=True)
    (case_root / "case.yaml").write_text(yaml_text, encoding="utf8")
    (case_root / file_name).write_text(code, encoding="utf8")


def test_build_seed_cases_converts_detect_and_clean_cases(tmp_path: Path) -> None:
    module = _load_module()
    benchmark_root = tmp_path / "cases"
    _write_case(
        benchmark_root,
        category="correctness",
        case_id="case-01",
        yaml_text=(
            'id: "case-01"\n'
            "category: correctness\n"
            "severity: high\n"
            'description: "Off-by-one range skips last element"\n'
            'expected_finding_regex: "range\\\\(len\\\\("\n'
            "expected_behavior: detect\n"
        ),
        file_name="defect.py",
        code="def total(items):\n    return items[0]\n",
    )
    _write_case(
        benchmark_root,
        category="clean",
        case_id="clean-01",
        yaml_text=(
            'id: "clean-01"\n'
            "category: clean\n"
            "severity: info\n"
            'description: "Clean arithmetic helpers"\n'
            'expected_finding_regex: ""\n'
            "expected_behavior: detect\n"
        ),
        file_name="code.py",
        code="def add(a, b):\n    return a + b\n",
    )

    cases = module.build_seed_cases(benchmark_root)

    assert [case.output_dir_name for case in cases] == ["seed-clean-clean-01", "seed-correctness-case-01"]
    detect_case = cases[1]
    assert "diff --git a/correctness/case-01/defect.py b/correctness/case-01/defect.py" in detect_case.diff_text
    assert detect_case.expected_payload == (
        {
            "path": "correctness/case-01/defect.py",
            "title_regex": "range\\(len\\(",
            "severity": None,
        },
    )
    assert cases[0].expected_payload == ()


def test_write_seed_cases_is_idempotent(tmp_path: Path) -> None:
    module = _load_module()
    cases = [
        module.GoldenSeedCase(
            case_id="case-01",
            output_dir_name="seed-correctness-case-01",
            diff_text="diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n@@ -0,0 +1 @@\n+print('x')",
            expected_payload=({"path": "x.py", "title_regex": "oops", "severity": None},),
        )
    ]

    module.write_seed_cases(cases, tmp_path)
    module.write_seed_cases(cases, tmp_path)

    case_root = tmp_path / "seed-correctness-case-01"
    assert (case_root / "diff.patch").read_text(encoding="utf8").endswith("\n")
    assert json.loads((case_root / "expected.json").read_text(encoding="utf8")) == [
        {"path": "x.py", "title_regex": "oops", "severity": None}
    ]
