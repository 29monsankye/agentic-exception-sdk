"""Dead letter queue for HARD_KILL envelopes — ring-buffer, drop-oldest.

Never rejects a new envelope. When full, drops the oldest entry and increments
dlq_dropped_oldest_total so operators can alert on overflow without losing the
most recent HARD_KILL context.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Protocol, runtime_checkable

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "AsyncInMemoryDLQ",
    "DeadLetterQueue",
    "InMemoryDLQ",
]


@runtime_checkable
class DeadLetterQueue(Protocol):
    """Protocol for dead-letter queues receiving HARD_KILL envelopes.

    DLQ implementations use drop-oldest semantics to ensure that the most
    recent HARD_KILL context is always preserved.
    """

    def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Publish a HARD_KILL envelope to the DLQ.

        Never raises. Drop-oldest semantics when full.

        Args:
            envelope: The HARD_KILL envelope to store.
        """
        ...


class InMemoryDLQ:
    """Ring-buffer in-memory dead letter queue.

    Drop-oldest semantics: when max_size is reached, the oldest envelope is
    evicted before the new one is appended. This ensures the most recent
    HARD_KILL envelopes survive overflow. dlq_dropped_oldest_total is
    incremented on each eviction so operators can alert.

    Thread-safe using threading.RLock.

    Args:
        max_size: Maximum number of envelopes to hold before dropping oldest.
            Default 1000.
    """

    def __init__(self, *, max_size: int = 1000) -> None:
        self._items: list[AgentExceptionEnvelope] = []
        self._lock = threading.RLock()
        self.max_size = max_size
        self.dlq_dropped_oldest_total: int = 0

    def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Publish an envelope to the DLQ, dropping oldest if full.

        Args:
            envelope: The envelope to store.
        """
        with self._lock:
            if len(self._items) >= self.max_size:
                self._items.pop(0)
                self.dlq_dropped_oldest_total += 1
            self._items.append(envelope)

    def drain(self) -> list[AgentExceptionEnvelope]:
        """Return and clear all envelopes. Used for testing and inspection.

        Returns:
            A copy of the current envelope list, oldest first.
        """
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def peek(self, n: int | None = None) -> list[AgentExceptionEnvelope]:
        """Return envelopes without removing them.

        Args:
            n: Maximum number of envelopes to return. None returns all.

        Returns:
            A copy of the selected envelopes. Limited results are newest first.
        """
        with self._lock:
            if n is None:
                return list(self._items)
            if n <= 0:
                return []
            return list(reversed(self._items[-n:]))

    @property
    def size(self) -> int:
        """Current number of envelopes in the DLQ."""
        with self._lock:
            return len(self._items)


class AsyncInMemoryDLQ:
    """Async ring-buffer in-memory dead letter queue using asyncio.Lock.

    Semantics identical to InMemoryDLQ but safe for use inside async coroutines.
    Never acquires a blocking thread lock from the event loop.

    Args:
        max_size: Maximum number of envelopes to hold before dropping oldest.
            Default 1000.
    """

    def __init__(self, *, max_size: int = 1000) -> None:
        self._items: list[AgentExceptionEnvelope] = []
        self._lock: asyncio.Lock | None = None
        self.max_size = max_size
        self.dlq_dropped_oldest_total: int = 0

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Asynchronously publish an envelope to the DLQ, dropping oldest if full.

        Args:
            envelope: The envelope to store.
        """
        async with self._get_lock():
            if len(self._items) >= self.max_size:
                self._items.pop(0)
                self.dlq_dropped_oldest_total += 1
            self._items.append(envelope)

    async def drain(self) -> list[AgentExceptionEnvelope]:
        """Asynchronously return and clear all envelopes.

        Returns:
            A copy of the current envelope list, oldest first.
        """
        async with self._get_lock():
            items = list(self._items)
            self._items.clear()
            return items

    async def peek(self, n: int | None = None) -> list[AgentExceptionEnvelope]:
        """Asynchronously return envelopes without removing them.

        Args:
            n: Maximum number of envelopes to return. None returns all.

        Returns:
            A copy of the selected envelopes. Limited results are newest first.
        """
        async with self._get_lock():
            if n is None:
                return list(self._items)
            if n <= 0:
                return []
            return list(reversed(self._items[-n:]))

    @property
    def size(self) -> int:
        """Current number of envelopes (snapshot — not guaranteed under contention)."""
        return len(self._items)
