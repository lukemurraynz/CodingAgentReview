"""Durable work dispatch via Service Bus (FR-017).

At-least-once delivery; consumers must be idempotent (run persistence is keyed
by run id, so redelivery upserts). Poison messages dead-letter after max retries.
"""

from __future__ import annotations

import json
import os
from typing import Any

QUEUE_NAME = "review-requests"


def _connection() -> str | None:
    return os.environ.get("HARNESS_SERVICEBUS_NS")


def _fully_qualified() -> str | None:
    ns = _connection()
    return f"{ns}.servicebus.windows.net" if ns else None


class ReviewQueuePublisher:
    """Enqueues review requests for the worker pool."""

    def enqueue(self, payload: dict[str, Any]):  # type: ignore[no-untyped-def]
        fqns = _fully_qualified()
        if not fqns:
            raise RuntimeError("HARNESS_SERVICEBUS_NS not configured — queue unavailable")
        from azure.identity.aio import DefaultAzureCredential
        from azure.servicebus import ServiceBusMessage
        from azure.servicebus.aio import ServiceBusClient

        client = ServiceBusClient(fqns, DefaultAzureCredential())

        async def _send() -> None:
            async with client:
                sender = client.get_queue_sender(QUEUE_NAME)
                async with sender:
                    await sender.send_messages(ServiceBusMessage(json.dumps(payload)))

        return _send()


class ReviewQueueConsumer:
    """Receives messages; handler exceptions leave the message for redelivery."""

    def receive(self, handler):  # type: ignore[no-untyped-def]
        conn = os.environ.get("HARNESS_SERVICEBUS_LISTEN_CONN")
        fqns = _fully_qualified()
        if not conn and not fqns:
            raise RuntimeError("Service Bus receive config missing (conn string or namespace)")
        from azure.servicebus import ServiceBusMessage  # noqa: F401
        from azure.servicebus.aio import AutoLockRenewer, ServiceBusClient
        from azure.identity.aio import DefaultAzureCredential

        if conn:
            client = ServiceBusClient.from_connection_string(conn)
        else:
            assert fqns is not None  # guarded above
            client = ServiceBusClient(fqns, DefaultAzureCredential())
        renewer = AutoLockRenewer(max_lock_renewal_duration=1800)

        async def _run() -> None:
            async with client:
                # Lock auto-renewal keeps long reviews from losing their lease mid-run.
                receiver = client.get_queue_receiver(QUEUE_NAME, auto_lock_renewer=renewer)
                async with receiver:
                    async for msg in receiver:
                        try:
                            await handler(json.loads(str(msg)))
                            await receiver.complete_message(msg)
                        except Exception:
                            # at-least-once: abandon → redelivery → dead-letter on max retries
                            await receiver.abandon_message(msg)

        return _run()
