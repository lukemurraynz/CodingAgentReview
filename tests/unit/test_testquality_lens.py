"""Deterministic test-quality lens tests — rule hits + clean misses."""

import pytest

from lenses import LENS_REGISTRY, LensContext, LensFile, TestQualityLens


def _ctx(*files: LensFile) -> LensContext:
    return LensContext(change_id="c1", repo_id="org/repo", files=list(files))


def _file(path: str, content: str, *, deleted: bool = False, patch_lines: tuple[str, ...] = ()) -> LensFile:
    return LensFile(
        path=path,
        content=content,
        deleted=deleted,
        added_lines=frozenset(range(1, content.count("\n") + 2)) if content else frozenset(),
        line_map=tuple(range(1, content.count("\n") + 2)) if content else (),
        patch_lines=patch_lines,
    )


PROD_ONLY = '''\
def normalize_name(name: str) -> str:
    return name.strip().lower()
'''

TEST_UPDATE = '''\
def test_normalize_name_trims_spaces() -> None:
    assert normalize_name(" A ") == "a"
'''

SKIP_TEST = '''\
import pytest

@pytest.mark.skip(reason="flaky")
def test_review_flow() -> None:
    assert True
'''


@pytest.mark.asyncio
class TestTestQualityLens:
    async def test_missing_corresponding_test_change_flagged(self):
        findings = await TestQualityLens().run(_ctx(_file("src/lenses/helpers.py", PROD_ONLY)))
        assert any(f.evidence[0].rule_id == "test-quality.missing-corresponding-tests" for f in findings)

    async def test_matching_test_change_suppresses_missing_test_finding(self):
        findings = await TestQualityLens().run(
            _ctx(_file("src/lenses/helpers.py", PROD_ONLY), _file("tests/unit/test_helpers.py", TEST_UPDATE))
        )
        assert not any(f.evidence[0].rule_id == "test-quality.missing-corresponding-tests" for f in findings)

    async def test_test_only_assertion_weakening_flagged(self):
        weakened = LensFile(
            path="tests/unit/test_runner.py",
            content="def test_runner() -> None:\n    assert result is True\n",
            added_lines=frozenset({1, 2}),
            line_map=(1, 2),
            patch_lines=(
                "@@ -1,3 +1,2 @@",
                "-    assert result.status_code == 200",
                "-    assert payload['ok'] is True",
                "+    assert result is True",
            ),
        )
        findings = await TestQualityLens().run(_ctx(weakened))
        hits = [f for f in findings if f.evidence[0].rule_id == "test-quality.test-only-weakening"]
        assert hits
        assert hits[0].evidence[0].metrics["removed_assertions"] == 2.0

    async def test_deleted_test_file_flagged(self):
        deleted = _file(
            "tests/unit/test_legacy.py",
            "",
            deleted=True,
            patch_lines=("@@ -1,2 +0,0 @@", "-def test_old():", "-    assert True"),
        )
        findings = await TestQualityLens().run(_ctx(deleted))
        assert any(f.evidence[0].rule_id == "test-quality.deleted-tests" for f in findings)

    async def test_skip_marker_flagged(self):
        findings = await TestQualityLens().run(_ctx(_file("tests/unit/test_review.py", SKIP_TEST)))
        assert any(f.evidence[0].rule_id == "test-quality.disabled-test-added" for f in findings)

    async def test_registered_in_registry(self):
        assert isinstance(LENS_REGISTRY["test_quality"], TestQualityLens)
