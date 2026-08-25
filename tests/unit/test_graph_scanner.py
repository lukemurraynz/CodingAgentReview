"""T068: repository discovery scanner for declared specifications."""

from pathlib import Path

from graph.scanner import ArtifactKind, discover_specification_artifacts


def test_discovers_specs_under_supported_locations(tmp_path: Path):
    spec_dir = tmp_path / "specs" / "security"
    spec_dir.mkdir(parents=True)
    (spec_dir / "auth.spec.json").write_text("{}", encoding="utf-8")
    (spec_dir / "specifications.json").write_text("[]", encoding="utf-8")
    harness_dir = tmp_path / ".harness"
    harness_dir.mkdir()
    (harness_dir / "specifications.json").write_text("[]", encoding="utf-8")
    (spec_dir / "ignore.txt").write_text("x", encoding="utf-8")

    discovered = discover_specification_artifacts(tmp_path)

    assert tuple(item.path for item in discovered) == (
        ".harness/specifications.json",
        "specs/security/auth.spec.json",
        "specs/security/specifications.json",
    )
    assert tuple(item.kind for item in discovered) == (
        ArtifactKind.MANIFEST,
        ArtifactKind.SPECIFICATION,
        ArtifactKind.MANIFEST,
    )


def test_empty_repo_has_no_artifacts(tmp_path: Path):
    assert discover_specification_artifacts(tmp_path) == ()
