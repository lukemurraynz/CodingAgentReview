"""Observability bootstrap — declared nowhere else (FR-031 / known-pitfall:
orphaned telemetry). No-op (returns False) when no connection string present,
so local runs stay clean; Azure apps get it via APPLICATIONINSIGHTS_CONNECTION_STRING.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def configure_telemetry() -> bool:
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.info("telemetry: no APPLICATIONINSIGHTS_CONNECTION_STRING — skipping export")
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor()
    logger.info("telemetry: Azure Monitor exporter active")
    return True
