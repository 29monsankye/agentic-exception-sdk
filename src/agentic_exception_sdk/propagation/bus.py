"""Exception propagation bus protocol and bounded in-memory implementations.

The in-memory bus is a development-safe default, not an unbounded queue.
It rejects new entries with PropagationBufferFullError when full.
The async variant uses asyncio.Lock — never a blocking thread lock.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Protocol, runtime_checkable

from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope

__all__ = [
    "AsyncExceptionPropagationBus",
    "AsyncInMemoryBus",
    "ExceptionPropagationBus",
    "InMemoryBus",
    "PropagationBufferFullError",
]


class PropagationBufferFullError(RuntimeError):
    """Raised when an in-memory propagation buffer reaches max_size."""


@runtime_checkable
class ExceptionPropagationBus(Protocol):
    """Synchronous propagation bus for exception envelopes."""

    def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Publish an envelope to the bus.

        Args:
            envelope: The exception envelope to publish.

        Raises:
            PropagationBufferFullError: When the in-memory buffer is at capacity.
        """
        ...


@runtime_checkable
class AsyncExceptionPropagationBus(Protocol):
    """Async propagation bus for exception envelopes.

    async_resilient() must not call a sync networked bus directly on the
    event loop. Use an async variant or adapter.
    """

    async def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Asynchronously publish an envelope to the bus.

        Args:
            envelope: The exception envelope to publish.
        """
        ...


class InMemoryBus:
    """Bounded synchronous in-memory propagation bus.

    Thread-safe using threading.RLock. Rejects new entries with
    PropagationBufferFullError when max_size is reached.

    Args:
        max_size: Maximum number of envelopes to hold. Default 1000.
    """

    def __init__(self, *, max_size: int = 1000) -> None:
        self._items: list[AgentExceptionEnvelope] = []
        self._lock = threading.RLock()
        self.max_size = max_size
        self.bus_publish_failures_total: int = 0

    def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Publish an envelope to the bus.

        Args:
            envelope: The exception envelope to publish.

        Raises:
            PropagationBufferFullError: When max_size is reached.
        """
        with self._lock:
            if len(self._items) >= self.max_size:
                self.bus_publish_failures_total += 1
                raise PropagationBufferFullError(
                    f"in-memory bus is full (max_size={self.max_size})"
                )
            self._items.append(envelope)

    def drain(self) -> list[AgentExceptionEnvelope]:
        """Return and clear all envelopes. Used for testing and inspection.

        Returns:
            A copy of the current envelope list.
        """
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    @property
    def size(self) -> int:
        """Current number of envelopes in the bus."""
        with self._lock:
            return len(self._items)


class AsyncInMemoryBus:
    """Bounded async in-memory propagation bus using asyncio.Lock.

    Never acquires a blocking thread lock from the event loop.

    Args:
        max_size: Maximum number of envelopes to hold. Default 1000.
    """

    def __init__(self, *, max_size: int = 1000) -> None:
        self._items: list[AgentExceptionEnvelope] = []
        self._lock: asyncio.Lock | None = None
        self.max_size = max_size

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def publish(self, envelope: AgentExceptionEnvelope) -> None:
        """Asynchronously publish an envelope to the bus.

        Args:
            envelope: The exception envelope to publish.

        Raises:
            PropagationBufferFullError: When max_size is reached.
        """
        async with self._get_lock():
            if len(self._items) >= self.max_size:
                raise PropagationBufferFullError(
                    f"async in-memory bus is full (max_size={self.max_size})"
                )
            self._items.append(envelope)

    async def drain(self) -> list[AgentExceptionEnvelope]:
        """Asynchronously return and clear all envelopes.

        Returns:
            A copy of the current envelope list.
        """
        async with self._get_lock():
            items = list(self._items)
            self._items.clear()
            return items

    @property
    def size(self) -> int:
        """Current number of envelopes (snapshot — not guaranteed under contention)."""
        return len(self._items)
