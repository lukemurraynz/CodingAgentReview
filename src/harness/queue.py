"""Durable work dispatch via Service Bus (FR-017).

At-least-once delivery; consumers must be idempotent (run persistence is keyed
by run id, so redelivery upserts). Poison messages dead-letter after max retries.
"""

from __future__ import annotations

import json
import logging
import os
from time import monotonic
from typing import Any

QUEUE_NAME = "review-requests"
logger = logging.getLogger(__name__)


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
    """Receive and dispatch review requests with at-least-once semantics.

    Handlers must be idempotent: any transient failure abandons the message for
    redelivery, so the same payload may be observed more than once.
    """

    def __init__(self, client_factory=None) -> None:  # type: ignore[no-untyped-def]
        self._client_factory = client_factory

    @staticmethod
    def _decode_message(message: Any) -> dict[str, Any]:
        body = getattr(message, "body", None)
        if body is None:
            return json.loads(str(message))
        if isinstance(body, str):
            return json.loads(body)
        if isinstance(body, (bytes, bytearray)):
            return json.loads(body.decode("utf-8"))
        parts: list[bytes] = []
        for chunk in body:
            if isinstance(chunk, bytes):
                parts.append(chunk)
            elif isinstance(chunk, bytearray):
                parts.append(bytes(chunk))
            else:
                parts.append(str(chunk).encode("utf-8"))
        return json.loads(b"".join(parts).decode("utf-8"))

    def _drain_seconds(self, drain_seconds: float | None) -> float | None:
        if drain_seconds is not None:
            return drain_seconds
        configured = os.environ.get("HARNESS_DRAIN_SECONDS")
        return float(configured) if configured else None

    def receive(self, handler, *, drain_seconds: float | None = None):  # type: ignore[no-untyped-def]
        conn = os.environ.get("HARNESS_SERVICEBUS_LISTEN_CONN")
        fqns = _fully_qualified()
        if self._client_factory is None and not conn and not fqns:
            raise RuntimeError("Service Bus receive config missing (conn string or namespace)")
        if self._client_factory is not None:
            client = self._client_factory()
        else:
            from azure.identity.aio import DefaultAzureCredential
            from azure.servicebus.aio import ServiceBusClient

            if conn:
                client = ServiceBusClient.from_connection_string(conn)
            else:
                assert fqns is not None  # guarded above
                client = ServiceBusClient(fqns, DefaultAzureCredential())

        async def _run() -> None:
            deadline = None
            effective_drain = self._drain_seconds(drain_seconds)
            if effective_drain is not None:
                deadline = monotonic() + effective_drain
            async with client:
                receiver = client.get_queue_receiver(QUEUE_NAME)
                async with receiver:
                    while True:
                        remaining = None if deadline is None else max(0.0, deadline - monotonic())
                        if deadline is not None and remaining == 0.0:
                            return
                        wait_time = 1.0 if remaining is None else min(1.0, remaining)
                        messages = await receiver.receive_messages(max_message_count=1, max_wait_time=wait_time)
                        if not messages:
                            if deadline is not None and monotonic() >= deadline:
                                return
                            continue
                        for msg in messages:
                            try:
                                payload = self._decode_message(msg)
                                await handler(payload)
                            except Exception as exc:
                                # at-least-once: abandon → redelivery → dead-letter on max retries
                                await receiver.abandon_message(msg)
                                logger.exception("review message abandoned for redelivery: %s", exc)
                            else:
                                await receiver.complete_message(msg)

        return _run()
