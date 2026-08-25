"""T069: declared specification loading with structured errors."""

from pathlib import Path

from graph.declared import load_specifications
from graph.scanner import discover_specification_artifacts


def test_loads_single_specification_and_manifest_entries(tmp_path: Path):
    spec_dir = tmp_path / "specs" / "security"
    spec_dir.mkdir(parents=True)
    (spec_dir / "auth.spec.json").write_text(
        """
        {
          "id": "auth-boundary",
          "title": "Authorization boundary",
          "applies_to": ["identity"],
          "path_patterns": ["src/auth/**/*.py"],
          "invariants": [{"id": "auth-tests", "applies_to": ["identity"]}]
        }
        """,
        encoding="utf-8",
    )
    (spec_dir / "specifications.json").write_text(
        '{"specifications": [{"id": "api-contract", "title": "API contract", "applies_to": ["api"]}]}',
        encoding="utf-8",
    )

    loaded = load_specifications(discover_specification_artifacts(tmp_path))

    assert [item.specification.id for item in loaded if item.specification] == ["auth-boundary", "api-contract"]
    assert all(not item.errors for item in loaded)


def test_malformed_files_surface_as_data(tmp_path: Path):
    spec_dir = tmp_path / "specs" / "broken"
    spec_dir.mkdir(parents=True)
    (spec_dir / "bad.spec.json").write_text("{not json}", encoding="utf-8")
    (spec_dir / "specifications.json").write_text('{"specifications": [{"title": "Missing id"}]}', encoding="utf-8")

    loaded = load_specifications(discover_specification_artifacts(tmp_path))

    assert [item.specification for item in loaded] == [None, None]
    assert "invalid JSON" in loaded[0].errors[0].message
    assert "id" in loaded[1].errors[0].message
