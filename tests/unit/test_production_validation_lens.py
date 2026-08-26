"""Production-validation lens tests — taxonomy rules hit + miss."""

import pytest

from graph import build_symbol_index
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

FAILURE_RESPONSE = '''\
import urllib.error
import urllib.request

def send_request(request):
    try:
        with urllib.request.urlopen(request) as response:
            return {"success": 200 <= response.status < 300}
    except urllib.error.HTTPError as error:
        return {
            "statusCode": error.code,
            "statusDescription": error.reason,
            "success": False,
        }
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

    async def test_explicit_failure_response_not_flagged_as_swallowed_success(self):
        ctx = _ctx(("http/client.py", FAILURE_RESPONSE))
        findings = await ProductionValidationLens().run(ctx)
        assert not any(
            f.evidence[0].rule_id == "prodval.swallowed-final-failure" for f in findings
        )

    async def test_registered_in_registry(self):
        assert isinstance(LENS_REGISTRY["production_validation"], ProductionValidationLens)

    async def test_repo_wide_symbol_index_keeps_unreferenced_registration_flagged(self, tmp_path):
        startup = tmp_path / "startup.py"
        startup.write_text(REGISTERED_NOT_INVOKED, encoding="utf-8")
        (tmp_path / "workers").mkdir(parents=True)
        (tmp_path / "workers" / "legacy.py").write_text(LEGACY_FILE, encoding="utf-8")

        ctx = LensContext(
            change_id="c1",
            repo_id="org/repo",
            files=[LensFile(path="startup.py", content=REGISTERED_NOT_INVOKED)],
            symbol_index=build_symbol_index(tmp_path),
        )

        findings = await ProductionValidationLens().run(ctx)

        assert any(f.evidence[0].rule_id == "prodval.registered-not-invoked" for f in findings)

    async def test_without_symbol_index_preserves_diff_local_behavior(self, tmp_path):
        startup = tmp_path / "startup.py"
        startup.write_text(REGISTERED_NOT_INVOKED, encoding="utf-8")
        (tmp_path / "workers").mkdir(parents=True)
        (tmp_path / "workers" / "legacy.py").write_text(LEGACY_FILE, encoding="utf-8")
        (tmp_path / "consumer.py").write_text(USED_FILE, encoding="utf-8")

        ctx = LensContext(
            change_id="c1",
            repo_id="org/repo",
            files=[LensFile(path="startup.py", content=REGISTERED_NOT_INVOKED)],
        )

        findings = await ProductionValidationLens().run(ctx)

        assert any(f.evidence[0].rule_id == "prodval.registered-not-invoked" for f in findings)

    async def test_repo_wide_symbol_index_suppresses_false_positive_when_caller_exists(self, tmp_path):
        startup = tmp_path / "startup.py"
        startup.write_text(REGISTERED_NOT_INVOKED, encoding="utf-8")
        (tmp_path / "workers").mkdir(parents=True)
        (tmp_path / "workers" / "legacy.py").write_text(LEGACY_FILE, encoding="utf-8")
        (tmp_path / "consumer.py").write_text(USED_FILE, encoding="utf-8")

        ctx = LensContext(
            change_id="c1",
            repo_id="org/repo",
            files=[LensFile(path="startup.py", content=REGISTERED_NOT_INVOKED)],
            symbol_index=build_symbol_index(tmp_path),
        )

        findings = await ProductionValidationLens().run(ctx)

        assert not any(f.evidence[0].rule_id == "prodval.registered-not-invoked" for f in findings)
