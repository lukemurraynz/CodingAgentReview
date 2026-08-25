"""Deterministic architecture lens tests — rule hits + clean misses."""

import pytest

from lenses import LENS_REGISTRY, ArchitectureLens, LensContext, LensFile


def _ctx(*files: tuple[str, str]) -> LensContext:
    return LensContext(
        change_id="c1",
        repo_id="org/repo",
        files=[
            LensFile(path=path, content=content, added_lines=frozenset(range(1, content.count("\n") + 2)))
            for path, content in files
        ],
    )


LAYERING_VIOLATION = '''\
from src.api.handlers import router

def decide_order(total: int) -> str:
    return router.name if total > 1 else "single"
'''

PUBLIC_API_CHANGE = '''\
def create_review_session() -> str:
    return "ok"
'''

LOW_LEVEL_CROSS_CUTTING = '''\
import logging

logger = logging.getLogger(__name__)

def build_entity(name: str) -> dict[str, str]:
    logger.info("build %s", name)
    return {"name": name}
'''

SMALL_CLEAN = '''\
def calculate_score(base: int, bonus: int) -> int:
    return base + bonus
'''


@pytest.mark.asyncio
class TestArchitectureLens:
    async def test_layering_violation_flagged(self):
        findings = await ArchitectureLens().run(_ctx(("src/domain/order_service.py", LAYERING_VIOLATION)))
        hits = [f for f in findings if f.evidence[0].rule_id == "architecture.layering-violation"]
        assert hits
        assert hits[0].severity.value == "high"

    async def test_public_api_change_without_hint_flagged(self):
        findings = await ArchitectureLens().run(_ctx(("src/api/review.py", PUBLIC_API_CHANGE)))
        assert any(f.evidence[0].rule_id == "architecture.public-api-change" for f in findings)

    async def test_cross_cutting_concern_in_low_level_layer_flagged(self):
        findings = await ArchitectureLens().run(_ctx(("src/models/entity.py", LOW_LEVEL_CROSS_CUTTING)))
        assert any(f.evidence[0].rule_id == "architecture.cross-cutting-layer" for f in findings)

    async def test_god_file_triggered_by_size_and_concentration(self):
        large = "\n".join(f"value_{index} = {index}" for index in range(160))
        findings = await ArchitectureLens().run(
            _ctx(("src/services/review_pipeline.py", large), ("src/utils.py", "x = 1\n"))
        )
        hits = [f for f in findings if f.evidence[0].rule_id == "architecture.god-file"]
        assert hits
        assert hits[0].evidence[0].metrics["change_share"] > 0.6

    async def test_docs_hint_suppresses_public_api_warning(self):
        findings = await ArchitectureLens().run(
            _ctx(("src/api/review.py", PUBLIC_API_CHANGE), ("docs/changelog.md", "version: v2\n"))
        )
        assert not any(f.evidence[0].rule_id == "architecture.public-api-change" for f in findings)

    async def test_small_clean_code_zero_findings(self):
        findings = await ArchitectureLens().run(_ctx(("src/services/score.py", SMALL_CLEAN)))
        assert findings == []

    async def test_registered_in_registry(self):
        assert isinstance(LENS_REGISTRY["architecture"], ArchitectureLens)
