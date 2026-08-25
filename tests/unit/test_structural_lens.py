"""Deterministic structural lens tests — every rule hit + clean-code misses."""

import pytest

from lenses import LENS_REGISTRY, LensContext, LensFile, StructuralLens


def _ctx(*files: tuple[str, str]) -> LensContext:
    return LensContext(
        change_id="c1",
        repo_id="org/repo",
        files=[LensFile(path=p, content=c, added_lines=frozenset(range(1, c.count("\n") + 2))) for p, c in files],
    )


NOOP_CASE = '''\
import logging
logger = logging.getLogger(__name__)

class _NoopEventSubscriber:
    def handle(self, event): ...

def build_router(subscriber=None):
    # production wiring instantiates the no-op directly
    subscriber = subscriber or _NoopEventSubscriber()
    return {"handler": subscriber.handle}
'''

SILENT_FALLBACK = '''\
import logging
logger = logging.getLogger(__name__)

def load_config(path):
    try:
        raw = open(path).read()
        return parse(raw)
    except Exception as e:
        logger.warning("config load failed: %s", e)
        continue_placeholder = None
    return {}
'''

FABRICATED_STATUS = '''\
def sync_records(source, target):
    status = "complete"
    return status
'''

CLEAN_CODE = '''\
def add(a: int, b: int) -> int:
    """Add two integers."""
    if a is None:
        raise ValueError("a required")
    try:
        result = a + b
    except TypeError:
        logger.error("type mismatch adding %r %r", a, b)
        raise
    return result
'''


@pytest.mark.asyncio
class TestRules:
    async def test_noop_instantiation_flagged_blocker(self):
        ctx = _ctx(("src/router.py", NOOP_CASE))
        findings = await StructuralLens().run(ctx)
        rule_hits = [f for f in findings if f.evidence[0].rule_id == "structural.noop-wiring"]
        assert rule_hits, "expected noop-wiring BLOCKER"
        assert any(f.severity.value == "blocker" for f in rule_hits)
        assert all(f.evidence[0].path == "src/router.py" for f in rule_hits)

    async def test_silent_fallback_flagged(self):
        ctx = _ctx(("cfg/loader.py", SILENT_FALLBACK))
        findings = await StructuralLens().run(ctx)
        assert any(f.evidence[0].rule_id == "structural.silent-fallback" for f in findings)

    async def test_fabricated_status_flagged(self):
        ctx = _ctx(("jobs/sync.py", FABRICATED_STATUS))
        findings = await StructuralLens().run(ctx)
        hits = [f for f in findings if f.evidence[0].rule_id == "structural.fabricated-status"]
        assert hits

    async def test_clean_code_zero_findings(self):
        ctx = _ctx(("math/ops.py", CLEAN_CODE))
        findings = await StructuralLens().run(ctx)
        assert findings == []

    async def test_file_growth_rule(self):
        big = "\n".join(f"x_{i} = {i}" for i in range(1100))
        ctx = _ctx(("legacy/big.py", big))
        findings = await StructuralLens().run(ctx)
        growth = [f for f in findings if f.evidence[0].rule_id == "structural.file-growth"]
        assert growth and growth[0].evidence[0].metrics["line_count"] == 1100.0

    async def test_deterministic_and_sorted(self):
        ctx = _ctx(("a.py", NOOP_CASE), ("b.py", SILENT_FALLBACK))
        r1 = await StructuralLens().run(ctx)
        r2 = await StructuralLens().run(ctx)
        assert [f.id for f in r1] == [f.id for f in r2]

    async def test_registered_in_registry(self):
        assert isinstance(LENS_REGISTRY["structural"], StructuralLens)
