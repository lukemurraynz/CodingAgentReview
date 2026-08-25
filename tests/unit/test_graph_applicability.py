"""T070: graph merge + applicability resolution."""

from pathlib import Path

from graph import (
    load_specifications,
    merge_specifications,
    resolve_applicable_specifications,
)
from graph.scanner import discover_specification_artifacts


def _write_spec(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def test_path_pattern_matches_change(tmp_path: Path):
    _write_spec(
        tmp_path / "specs" / "api" / "api.spec.json",
        '{"id": "api-contract", "title": "API contract", "path_patterns": ["src/api/**/*.py"]}',
    )
    graph = merge_specifications(load_specifications(discover_specification_artifacts(tmp_path)))

    applicable = resolve_applicable_specifications(graph, changed_paths=["src/api/routes.py"])

    assert [item.specification.id for item in applicable] == ["api-contract"]
    assert applicable[0].matched_paths == ("src/api/routes.py",)


def test_non_matching_change_returns_empty(tmp_path: Path):
    _write_spec(
        tmp_path / "specs" / "api" / "api.spec.json",
        '{"id": "api-contract", "title": "API contract", "path_patterns": ["src/api/**/*.py"]}',
    )
    graph = merge_specifications(load_specifications(discover_specification_artifacts(tmp_path)))

    assert resolve_applicable_specifications(graph, changed_paths=["README.md"]) == ()


def test_overlapping_specs_both_apply(tmp_path: Path):
    _write_spec(
        tmp_path / "specs" / "security" / "auth.spec.json",
        '{"id": "auth-boundary", "title": "Auth", "applies_to": ["identity"], '
        '"invariants": [{"id": "auth-tests", "applies_to": ["identity"]}]}',
    )
    _write_spec(
        tmp_path / "specs" / "service" / "service.spec.json",
        '{"id": "service-shape", "title": "Service", "path_patterns": ["src/services/**/*.py"]}',
    )
    graph = merge_specifications(load_specifications(discover_specification_artifacts(tmp_path)))

    applicable = resolve_applicable_specifications(
        graph,
        changed_paths=["src/services/auth.py"],
        resource_types=["identity"],
    )

    assert [item.specification.id for item in applicable] == ["auth-boundary", "service-shape"]
    assert applicable[0].matched_invariants == ("auth-tests",)


def test_empty_graph_returns_empty_for_any_change():
    assert resolve_applicable_specifications(merge_specifications(()), changed_paths=["src/app.py"]) == ()
