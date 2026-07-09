"""Tests for dead-letter queue implementations."""

from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentic_exception_sdk.propagation.dlq import AsyncInMemoryDLQ, InMemoryDLQ


class TestAsyncInMemoryDLQ:
    @pytest.mark.asyncio
    async def test_publish_and_drain_preserve_fifo_order(self, exception_envelope):
        dlq = AsyncInMemoryDLQ(max_size=3)

        await dlq.publish(exception_envelope)

        assert dlq.size == 1
        assert await dlq.drain() == [exception_envelope]
        assert dlq.size == 0
        assert await dlq.drain() == []

    @pytest.mark.asyncio
    async def test_drop_oldest_when_full(self, exception_envelope):
        dlq = AsyncInMemoryDLQ(max_size=2)
        envelopes = [
            exception_envelope.model_copy(update={"exception_id": f"dlq-{index}"})
            for index in range(3)
        ]

        for envelope in envelopes:
            await dlq.publish(envelope)

        assert [env.exception_id for env in await dlq.drain()] == ["dlq-1", "dlq-2"]
        assert dlq.dlq_dropped_oldest_total == 1

    @pytest.mark.asyncio
    async def test_concurrent_publish_never_exceeds_max_size(self, exception_envelope):
        dlq = AsyncInMemoryDLQ(max_size=5)
        envelopes = [
            exception_envelope.model_copy(update={"exception_id": f"dlq-{index}"})
            for index in range(20)
        ]

        await asyncio.gather(*(dlq.publish(envelope) for envelope in envelopes))

        assert dlq.size == 5
        assert dlq.dlq_dropped_oldest_total == 15
        assert [env.exception_id for env in await dlq.drain()] == [
            "dlq-15",
            "dlq-16",
            "dlq-17",
            "dlq-18",
            "dlq-19",
        ]

    @pytest.mark.asyncio
    async def test_peek_is_async_and_non_destructive(self, exception_envelope):
        assert inspect.iscoroutinefunction(AsyncInMemoryDLQ.peek)

        dlq = AsyncInMemoryDLQ(max_size=10)
        envelopes = [
            exception_envelope.model_copy(update={"exception_id": f"dlq-{index}"})
            for index in range(5)
        ]
        for envelope in envelopes:
            await dlq.publish(envelope)

        assert [env.exception_id for env in await dlq.peek(3)] == [
            "dlq-4",
            "dlq-3",
            "dlq-2",
        ]
        assert dlq.size == 5
        assert await dlq.peek(0) == []
        assert [env.exception_id for env in await dlq.peek()] == [
            "dlq-0",
            "dlq-1",
            "dlq-2",
            "dlq-3",
            "dlq-4",
        ]
        assert len(await dlq.drain()) == 5
        assert await dlq.peek() == []


def test_sync_dlq_satisfies_dead_letter_queue_protocol(exception_envelope):
    dlq = InMemoryDLQ(max_size=1)
    dlq.publish(exception_envelope)

    assert dlq.drain() == [exception_envelope]


def test_sync_dlq_peek_is_non_destructive_and_limited(exception_envelope):
    dlq = InMemoryDLQ(max_size=10)
    envelopes = [
        exception_envelope.model_copy(update={"exception_id": f"dlq-{index}"})
        for index in range(5)
    ]
    for envelope in envelopes:
        dlq.publish(envelope)

    assert [env.exception_id for env in dlq.peek(3)] == ["dlq-4", "dlq-3", "dlq-2"]
    assert dlq.size == 5
    assert dlq.peek(0) == []
    assert [env.exception_id for env in dlq.peek()] == [
        "dlq-0",
        "dlq-1",
        "dlq-2",
        "dlq-3",
        "dlq-4",
    ]
    assert len(dlq.drain()) == 5
    assert dlq.peek() == []


def test_sync_dlq_concurrent_peek_and_publish_is_safe(exception_envelope):
    dlq = InMemoryDLQ(max_size=100)
    envelopes = [
        exception_envelope.model_copy(update={"exception_id": f"dlq-{index}"})
        for index in range(50)
    ]

    def publish_all() -> None:
        for envelope in envelopes:
            dlq.publish(envelope)

    def peek_repeatedly() -> None:
        for _ in range(50):
            assert len(dlq.peek(3)) <= 3

    with ThreadPoolExecutor(max_workers=2) as executor:
        publish_future = executor.submit(publish_all)
        peek_future = executor.submit(peek_repeatedly)
        publish_future.result()
        peek_future.result()

    assert dlq.size == 50
