"""T073: review-rule discovery, loading, resolution, and brief building."""

from pathlib import Path

from graph import build_lens_briefs, load_rules, merge_specifications, resolve_applicable_rules
from graph.scanner import ArtifactKind, discover_specification_artifacts


def _write_rule(path: Path, *, frontmatter: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def test_discovers_rules_alongside_specifications(tmp_path: Path) -> None:
    harness_rules = tmp_path / ".harness" / "rules"
    _write_rule(
        harness_rules / "db.md",
        frontmatter='id: no-sync-db-calls\nseverity: major\napplies_to: ["src/db/**"]\nlens: security',
        body="Never call the database synchronously from request handlers.",
    )
    (tmp_path / ".harness" / "specifications.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "specifications.json").write_text("[]", encoding="utf-8")

    discovered = discover_specification_artifacts(tmp_path)

    assert tuple(item.path for item in discovered) == (".harness/rules/db.md", ".harness/specifications.json")
    assert tuple(item.kind for item in discovered) == (ArtifactKind.RULE, ArtifactKind.MANIFEST)


def test_loads_rule_frontmatter_and_normalizes_aliases(tmp_path: Path) -> None:
    frontmatter = (
        'id: no-sync-db-calls\nseverity: major\n'
        'applies_to: ["src/db/**", "!src/db/migrations/**"]\n'
        'lens: security'
    )
    _write_rule(
        tmp_path / ".harness" / "rules" / "db.md",
        frontmatter=frontmatter,
        body="Never introduce synchronous database calls in request handlers.",
    )

    loaded = load_rules(discover_specification_artifacts(tmp_path))

    assert len(loaded) == 1
    assert not loaded[0].errors
    assert loaded[0].rule is not None
    assert loaded[0].rule.id == "no-sync-db-calls"
    assert loaded[0].rule.severity.value == "high"
    assert loaded[0].rule.lens == "security"
    assert loaded[0].rule.applies_to == ["src/db/**", "!src/db/migrations/**"]


def test_rule_validation_errors_surface_as_data(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".harness" / "rules"
    _write_rule(
        rules_dir / "missing-id.md",
        frontmatter='severity: blocker\napplies_to: ["src/db/**"]\nlens: general',
        body="Body exists.",
    )
    _write_rule(
        rules_dir / "bad-severity.md",
        frontmatter='id: bad-severity\nseverity: urgent\napplies_to: ["src/db/**"]\nlens: general',
        body="Body exists.",
    )
    _write_rule(
        rules_dir / "empty-body.md",
        frontmatter='id: empty-body\nseverity: info\napplies_to: ["src/db/**"]\nlens: general',
        body="   ",
    )
    _write_rule(
        rules_dir / "bad-glob.md",
        frontmatter="id: bad-glob\nseverity: info\napplies_to: ['src\\db\\**']\nlens: general",
        body="Body exists.",
    )

    loaded = load_rules(discover_specification_artifacts(tmp_path))
    by_path = {item.source_path: item for item in loaded}

    assert all(item.rule is None for item in loaded)
    assert "id" in by_path[".harness/rules/missing-id.md"].errors[0].message
    assert "severity" in by_path[".harness/rules/bad-severity.md"].errors[0].message
    assert "instruction body must not be empty" in by_path[".harness/rules/empty-body.md"].errors[0].message
    assert "must use '/' separators" in by_path[".harness/rules/bad-glob.md"].errors[0].message


def test_malformed_rule_frontmatter_surfaces_as_data(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".harness" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "bad-yaml.md").write_text(
        "---\nid: [unterminated\nseverity: blocker\napplies_to: [\"src/**\"]\nlens: general\n---\nBody.\n",
        encoding="utf-8",
    )

    loaded = load_rules(discover_specification_artifacts(tmp_path))

    assert len(loaded) == 1
    assert loaded[0].rule is None
    assert loaded[0].errors[0].message == "rule frontmatter contains invalid YAML"


def test_duplicate_rule_ids_surface_as_errors(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".harness" / "rules"
    frontmatter = 'id: duplicate\nseverity: info\napplies_to: ["src/**"]\nlens: general'
    _write_rule(rules_dir / "first.md", frontmatter=frontmatter, body="First body.")
    _write_rule(rules_dir / "second.md", frontmatter=frontmatter, body="Second body.")

    loaded = load_rules(discover_specification_artifacts(tmp_path))

    assert [item.rule for item in loaded] == [None, None]
    assert all(item.errors[0].message == "duplicate rule id: duplicate" for item in loaded)


def test_negation_globs_exclude_matching_paths(tmp_path: Path) -> None:
    frontmatter = (
        'id: no-sync-db-calls\nseverity: blocker\n'
        'applies_to: ["src/db/**", "!src/db/migrations/**"]\n'
        'lens: security'
    )
    _write_rule(
        tmp_path / ".harness" / "rules" / "db.md",
        frontmatter=frontmatter,
        body="Never introduce synchronous database calls in request handlers.",
    )
    graph = merge_specifications((), load_rules(discover_specification_artifacts(tmp_path)))

    applicable = resolve_applicable_rules(
        graph,
        changed_paths=["src/db/query.py", "src/db/migrations/001_init.py"],
    )

    assert [item.rule.id for item in applicable] == ["no-sync-db-calls"]
    assert applicable[0].matched_paths == ("src/db/query.py",)


def test_rules_are_ordered_blocker_first_then_deterministically(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".harness" / "rules"
    _write_rule(
        rules_dir / "b-rule.md",
        frontmatter='id: block-first\nseverity: blocker\napplies_to: ["src/**"]\nlens: general',
        body="Blockers first.",
    )
    _write_rule(
        rules_dir / "a-rule.md",
        frontmatter='id: alpha-major\nseverity: major\napplies_to: ["src/**"]\nlens: security',
        body="Major before info.",
    )
    _write_rule(
        rules_dir / "z-rule.md",
        frontmatter='id: zeta-info\nseverity: info\napplies_to: ["src/**"]\nlens: architecture',
        body="Informational rule.",
    )
    graph = merge_specifications((), load_rules(discover_specification_artifacts(tmp_path)))

    applicable = resolve_applicable_rules(graph, changed_paths=["src/app.py"])

    assert [item.rule.id for item in applicable] == ["block-first", "alpha-major", "zeta-info"]


def test_build_lens_briefs_groups_rules_deterministically(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".harness" / "rules"
    _write_rule(
        rules_dir / "security.md",
        frontmatter='id: security-guard\nseverity: blocker\napplies_to: ["src/**"]\nlens: security',
        body="Always protect secrets at the boundary.",
    )
    _write_rule(
        rules_dir / "general.md",
        frontmatter='id: docs-needed\nseverity: info\napplies_to: ["src/**"]\nlens: general',
        body="Document non-obvious trade-offs in the changed code.",
    )
    graph = merge_specifications((), load_rules(discover_specification_artifacts(tmp_path)))

    briefs = build_lens_briefs(resolve_applicable_rules(graph, changed_paths=["src/app.py"]))

    assert briefs == {
        "general": "[info] docs-needed: Document non-obvious trade-offs in the changed code.",
        "security": "[blocker] security-guard: Always protect secrets at the boundary.",
    }
