"""Production-validation lens tests — taxonomy rules hit + miss."""

import pytest

from lenses import LENS_REGISTRY, LensContext, LensFile, ProductionValidationLens


def _ctx(*files: tuple[str, str]) -> LensContext:
    return LensContext(
        change_id="c1",
        repo_id="org/repo",
        files=[LensFile(path=p, content=c, added_lines=frozenset(range(1, c.count("\n") + 2))) for p, c in files],
    )


REGISTERED_NOT_INVOKED = '''\
from di import container
from workers.legacy import LegacyExporter

def configure():
    container.add_singleton(LegacyExporter, LegacyExporter)
'''

SWALLOWED_FINAL_FAILURE = '''\
import logging
logger = logging.getLogger(__name__)

async def publish_batch(client, items):
    try:
        await client.send(items)
    except Exception as e:
        logger.warning("send failed: %s", e)
        return "complete"
'''

TELEMETRY_GAP = '''\
import logging
logger = logging.getLogger(__name__)

def get_value(cache, key):
    logger.info("lookup %s", key)
    if cache is None:
        return fallback_load(key)

def fallback_load(key):
    return None
'''

CLEAN = '''\
import logging
logger = logging.getLogger(__name__)

async def send(client, items):
    try:
        await client.send(items)
    except Exception as e:
        logger.error("send failed: %s", e)
        raise
    return "ok"
'''


LEGACY_FILE = "class LegacyExporter:\n    def export(self): ...\n"
USED_FILE = (
    "from workers.legacy import LegacyExporter\n\n"
    "def run():\n    exporter = LegacyExporter()\n    return exporter.export()\n"
)


@pytest.mark.asyncio
class TestRules:
    async def test_registered_not_invoked(self):
        ctx = _ctx(("startup.py", REGISTERED_NOT_INVOKED), ("workers/legacy.py", LEGACY_FILE))
        findings = await ProductionValidationLens().run(ctx)
        assert any(f.evidence[0].rule_id == "prodval.registered-not-invoked" for f in findings)

    async def test_invoked_component_not_flagged(self):
        ctx = _ctx(("startup.py", REGISTERED_NOT_INVOKED), ("workers/legacy.py", USED_FILE))
        findings = await ProductionValidationLens().run(ctx)
        assert not any(f.evidence[0].rule_id == "prodval.registered-not-invoked" for f in findings)

    async def test_swallowed_final_failure(self):
        ctx = _ctx(("bus/publish.py", SWALLOWED_FINAL_FAILURE))
        findings = await ProductionValidationLens().run(ctx)
        assert any(f.evidence[0].rule_id == "prodval.swallowed-final-failure" for f in findings)

    async def test_missing_telemetry_fallback(self):
        ctx = _ctx(("svc/cache.py", TELEMETRY_GAP))
        findings = await ProductionValidationLens().run(ctx)
        assert any(f.evidence[0].rule_id == "prodval.missing-telemetry-fallback" for f in findings)

    async def test_clean_reraise_not_flagged(self):
        ctx = _ctx(("bus/publish.py", CLEAN))
        findings = await ProductionValidationLens().run(ctx)
        assert not any(
            f.evidence[0].rule_id in ("prodval.swallowed-final-failure",) for f in findings
        )

    async def test_registered_in_registry(self):
        assert isinstance(LENS_REGISTRY["production_validation"], ProductionValidationLens)
