import json

import pytest

from harness.queue import ReviewQueueConsumer


class FakeMessage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")


class FakeReceiver:
    def __init__(self, batches: list[list[FakeMessage]]) -> None:
        self._batches = list(batches)
        self.completed: list[FakeMessage] = []
        self.abandoned: list[FakeMessage] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def receive_messages(self, *, max_message_count: int, max_wait_time: float):
        del max_message_count, max_wait_time
        return self._batches.pop(0) if self._batches else []

    async def complete_message(self, msg: FakeMessage) -> None:
        self.completed.append(msg)

    async def abandon_message(self, msg: FakeMessage) -> None:
        self.abandoned.append(msg)


class FakeClient:
    def __init__(self, receiver: FakeReceiver) -> None:
        self.receiver = receiver

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def get_queue_receiver(self, queue_name: str, auto_lock_renewer=None) -> FakeReceiver:
        del queue_name, auto_lock_renewer
        return self.receiver


@pytest.mark.asyncio
async def test_receive_completes_on_success() -> None:
    receiver = FakeReceiver([[FakeMessage({"changeId": "c1"})], []])
    handled: list[dict[str, object]] = []
    consumer = ReviewQueueConsumer(client_factory=lambda: FakeClient(receiver))

    async def handler(payload: dict[str, object]) -> None:
        handled.append(payload)

    await consumer.receive(handler, drain_seconds=0.01)

    assert handled == [{"changeId": "c1"}]
    assert len(receiver.completed) == 1
    assert not receiver.abandoned


@pytest.mark.asyncio
async def test_receive_abandons_on_handler_failure() -> None:
    receiver = FakeReceiver([[FakeMessage({"changeId": "c1"})], []])
    consumer = ReviewQueueConsumer(client_factory=lambda: FakeClient(receiver))

    async def handler(payload: dict[str, object]) -> None:
        raise RuntimeError(f"boom {payload['changeId']}")

    await consumer.receive(handler, drain_seconds=0.01)

    assert not receiver.completed
    assert len(receiver.abandoned) == 1


@pytest.mark.asyncio
async def test_receive_exits_cleanly_when_draining_idle_queue() -> None:
    receiver = FakeReceiver([[]])
    consumer = ReviewQueueConsumer(client_factory=lambda: FakeClient(receiver))

    async def handler(payload: dict[str, object]) -> None:
        raise AssertionError(f"unexpected payload {payload}")

    await consumer.receive(handler, drain_seconds=0.0)

    assert not receiver.completed
    assert not receiver.abandoned
