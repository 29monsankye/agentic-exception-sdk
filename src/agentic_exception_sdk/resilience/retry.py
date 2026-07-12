"""Retry policy protocol and exponential backoff implementation.

Retry-After support prevents avoidable escalation during provider rate limits.
A rate-limited response that says "retry after 30 seconds" must not be retried
after 0.5 seconds and escalated to ISSUE.

Retries are safe only for idempotent operations. Non-idempotent side-effecting
tools must use idempotency keys, compensating transactions, or avoid automatic
retry entirely.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

_log = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "ExponentialBackoffRetry",
    "InMemoryRetryInFlightTracker",
    "NoOpRetry",
    "RetryContext",
    "RetryInFlightEntry",
    "RetryInFlightTracker",
    "RetryPolicy",
]


@dataclass(frozen=True)
class RetryContext:
    """Context passed to the optional idempotency key function on each attempt.

    Attributes:
        correlation_id: End-to-end trace ID from the enclosing resilient() call.
        agent_id: Agent identifier from the enclosing resilient() call.
        tool_name: Canonical tool name being retried.
        idempotency_key: Pre-supplied idempotency key, if any.
        on_retry: Optional zero-argument hook called before each retry sleep.
        attempt: Current attempt number (1-based).
    """

    correlation_id: str | None = None
    agent_id: str | None = None
    tool_name: str | None = None
    idempotency_key: str | None = None
    on_retry: Callable[[], None] | None = None
    attempt: int = 1


@dataclass(frozen=True)
class RetryInFlightEntry:
    """Snapshot of a retry currently waiting for its next attempt."""

    correlation_id: str
    agent_id: str
    tool_name: str | None
    attempt: int
    max_attempts: int
    error_type: str
    started_at: datetime
    next_retry_at: datetime | None


@runtime_checkable
class RetryInFlightTracker(Protocol):
    """Protocol for tracking retry attempts currently waiting between attempts."""

    def record_attempt(self, entry: RetryInFlightEntry) -> None:
        """Record or replace the active retry entry."""
        ...

    def complete(self, correlation_id: str) -> None:
        """Remove active retry state for the correlation ID."""
        ...

    def list_active(self) -> list[RetryInFlightEntry]:
        """Return a snapshot of active retry entries."""
        ...


class InMemoryRetryInFlightTracker:
    """Thread-safe in-memory retry in-flight tracker."""

    def __init__(self) -> None:
        self._entries: dict[str, RetryInFlightEntry] = {}
        self._lock = threading.RLock()

    def record_attempt(self, entry: RetryInFlightEntry) -> None:
        """Record or replace the active retry entry."""
        with self._lock:
            self._entries[entry.correlation_id] = entry

    def complete(self, correlation_id: str) -> None:
        """Remove active retry state for the correlation ID."""
        with self._lock:
            self._entries.pop(correlation_id, None)

    def list_active(self) -> list[RetryInFlightEntry]:
        """Return a thread-safe snapshot of active retry entries."""
        with self._lock:
            return list(self._entries.values())


@runtime_checkable
class RetryPolicy(Protocol):
    """Protocol for synchronous and asynchronous retry strategies."""

    def execute(
        self,
        fn: Callable[[], T],
        *,
        context: RetryContext | None = None,
    ) -> T:
        """Execute fn with retry logic.

        Args:
            fn: Zero-argument callable to retry.
            context: Optional retry context for idempotency key generation.

        Returns:
            The return value of fn on success.

        Raises:
            Exception: Re-raises the last exception when all attempts are exhausted.
        """
        ...

    async def async_execute(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        context: RetryContext | None = None,
    ) -> T:
        """Async version of execute.

        Args:
            fn: Zero-argument async callable to retry.
            context: Optional retry context for idempotency key generation.

        Returns:
            The awaited return value of fn on success.

        Raises:
            Exception: Re-raises the last exception when all attempts are exhausted.
        """
        ...


class NoOpRetry:
    """Executes fn exactly once with no retry logic."""

    def execute(
        self,
        fn: Callable[[], T],
        *,
        context: RetryContext | None = None,
    ) -> T:
        """Execute fn once without any retry.

        Args:
            fn: Zero-argument callable to execute.
            context: Ignored.

        Returns:
            The return value of fn.
        """
        return fn()

    async def async_execute(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        context: RetryContext | None = None,
    ) -> T:
        """Execute async fn once without any retry.

        Args:
            fn: Zero-argument async callable to execute.
            context: Ignored.

        Returns:
            The awaited return value of fn.
        """
        return await fn()


def _parse_retry_after(
    exc: BaseException,
    max_retry_after_seconds: float,
) -> float:
    """Parse Retry-After header from the exception's response attribute, if available.

    Supports both delta-seconds (integer string) and HTTP-date formats.
    Malformed values, None results, and negative delta-seconds all clamp to 0.
    The result is capped at max_retry_after_seconds to prevent header abuse.

    Args:
        exc: The exception that may expose response.headers["Retry-After"].
        max_retry_after_seconds: Maximum allowed retry-after delay in seconds.

    Returns:
        Parsed retry-after seconds, clamped to [0, max_retry_after_seconds].
        Returns 0.0 when no Retry-After header is present.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return 0.0
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0.0

    retry_after = headers.get("Retry-After")
    if retry_after is None:
        return 0.0

    # Try delta-seconds first
    try:
        delta = int(retry_after)
        if delta < 0:
            return 0.0
        return min(float(delta), max_retry_after_seconds)
    except (ValueError, TypeError):
        pass

    # Try HTTP-date via email.utils.parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(retry_after)
        if dt is None:
            return 0.0
        seconds = dt.timestamp() - time.time()
        if seconds < 0:
            return 0.0
        return min(seconds, max_retry_after_seconds)
    except (ValueError, TypeError):
        return 0.0


@dataclass
class ExponentialBackoffRetry:
    """Exponential backoff retry with optional jitter, Retry-After support, and idempotency hooks.

    The jitter random source is random.SystemRandom for non-deterministic behavior.
    Deterministic PRNGs are allowed only in tests with explicit seeding.

    Args:
        max_attempts: Total attempts including the first. Default 3.
        base_delay_seconds: Initial delay before first retry. Doubles each attempt.
        max_delay_seconds: Cap on computed exponential delay before jitter is added.
        jitter: Whether to add random jitter to each delay. Default True.
        retryable_exceptions: Exception types that trigger a retry.
        max_retry_after_seconds: Cap on Retry-After delay to prevent header abuse. Default 60.
        idempotency_key_fn: Optional callable(RetryContext) -> str for idempotency key tracking.
        budget_watchdog: Optional BudgetWatchdog checked before each sleep.
        inflight_tracker: Optional tracker for retries waiting between attempts.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0
    jitter: bool = True
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (TimeoutError, ConnectionError, OSError)
    )
    max_retry_after_seconds: float = 60.0
    idempotency_key_fn: Callable[[RetryContext], str] | None = None
    budget_watchdog: Any | None = None
    inflight_tracker: RetryInFlightTracker | None = None

    def _compute_delay(self, attempt: int, exc: BaseException) -> float:
        """Compute delay for this attempt with Retry-After support and optional jitter.

        Args:
            attempt: 1-based attempt number (1 = first retry = second call).
            exc: The exception that triggered the retry.

        Returns:
            Delay in seconds including any jitter.
        """
        exponential = min(
            self.base_delay_seconds * (2 ** (attempt - 1)),
            self.max_delay_seconds,
        )
        retry_after = _parse_retry_after(exc, self.max_retry_after_seconds)
        delay = max(exponential, retry_after)
        if self.jitter:
            jitter_amount = random.SystemRandom().uniform(0, delay * 0.5)
            delay = delay + jitter_amount
        return cast("float", delay)

    def _check_budget(self, delay: float) -> None:
        """Check budget watchdog before sleeping, if configured.

        Args:
            delay: Proposed sleep duration in seconds.

        Raises:
            BudgetExhaustedError: If the remaining budget cannot cover the sleep.
        """
        if self.budget_watchdog is not None:
            self.budget_watchdog.check_before_sleep(delay)

    def execute(
        self,
        fn: Callable[[], T],
        *,
        context: RetryContext | None = None,
    ) -> T:
        """Execute fn with synchronous exponential backoff retry.

        Args:
            fn: Zero-argument callable to execute.
            context: Optional retry context for idempotency key generation.

        Returns:
            The return value of fn on success.

        Raises:
            Exception: Re-raises the last exception when max_attempts is exhausted.
        """
        last_exc: BaseException | None = None
        # Resolved once so the finally clears the same in-flight key that
        # _record_attempt writes — even when fn() raises a *non-retryable*
        # exception (e.g. BudgetExhaustedError) that exits the loop early.
        correlation_key = (context.correlation_id if context else None) or ""
        try:
            for attempt in range(1, self.max_attempts + 1):
                ctx = RetryContext(
                    correlation_id=context.correlation_id if context else None,
                    agent_id=context.agent_id if context else None,
                    tool_name=context.tool_name if context else None,
                    idempotency_key=context.idempotency_key if context else None,
                    on_retry=context.on_retry if context else None,
                    attempt=attempt,
                )
                if self.idempotency_key_fn is not None and attempt > 1:
                    self.idempotency_key_fn(ctx)

                try:
                    return fn()
                except self.retryable_exceptions as exc:
                    last_exc = exc
                    if attempt < self.max_attempts:
                        delay = self._compute_delay(attempt, exc)
                        self._check_budget(delay)
                        self._record_attempt(ctx, exc, delay)
                        if ctx.on_retry is not None:
                            ctx.on_retry()
                        _log.debug(
                            "retry attempt %s/%s tool=%s delay=%.3fs",
                            attempt,
                            self.max_attempts,
                            ctx.tool_name,
                            delay,
                        )
                        time.sleep(delay)
            if last_exc is None:  # pragma: no cover - max_attempts validation prevents this path
                raise RuntimeError("retry policy exhausted without capturing an exception")
            raise last_exc
        finally:
            self._complete(correlation_key)

    async def async_execute(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        context: RetryContext | None = None,
    ) -> T:
        """Execute async fn with asynchronous exponential backoff retry.

        Args:
            fn: Zero-argument async callable to execute.
            context: Optional retry context for idempotency key generation.

        Returns:
            The awaited return value of fn on success.

        Raises:
            Exception: Re-raises the last exception when max_attempts is exhausted.
        """
        last_exc: BaseException | None = None
        correlation_key = (context.correlation_id if context else None) or ""
        try:
            for attempt in range(1, self.max_attempts + 1):
                ctx = RetryContext(
                    correlation_id=context.correlation_id if context else None,
                    agent_id=context.agent_id if context else None,
                    tool_name=context.tool_name if context else None,
                    idempotency_key=context.idempotency_key if context else None,
                    on_retry=context.on_retry if context else None,
                    attempt=attempt,
                )
                if self.idempotency_key_fn is not None and attempt > 1:
                    self.idempotency_key_fn(ctx)

                try:
                    return await fn()
                except self.retryable_exceptions as exc:
                    last_exc = exc
                    if attempt < self.max_attempts:
                        delay = self._compute_delay(attempt, exc)
                        self._check_budget(delay)
                        self._record_attempt(ctx, exc, delay)
                        if ctx.on_retry is not None:
                            ctx.on_retry()
                        _log.debug(
                            "async retry attempt %s/%s tool=%s delay=%.3fs",
                            attempt,
                            self.max_attempts,
                            ctx.tool_name,
                            delay,
                        )
                        await asyncio.sleep(delay)
            if last_exc is None:  # pragma: no cover - max_attempts validation prevents this path
                raise RuntimeError("async retry policy exhausted without capturing an exception")
            raise last_exc
        finally:
            self._complete(correlation_key)

    def _record_attempt(
        self,
        context: RetryContext,
        exc: BaseException,
        delay_seconds: float,
    ) -> None:
        if self.inflight_tracker is None:
            return
        now = datetime.now(UTC)
        self.inflight_tracker.record_attempt(
            RetryInFlightEntry(
                correlation_id=context.correlation_id or "",
                agent_id=context.agent_id or "unknown",
                tool_name=context.tool_name,
                attempt=context.attempt,
                max_attempts=self.max_attempts,
                error_type=type(exc).__name__,
                started_at=now,
                next_retry_at=now + timedelta(seconds=delay_seconds),
            )
        )

    def _complete(self, correlation_id: str | None) -> None:
        if self.inflight_tracker is None:
            return
        self.inflight_tracker.complete(correlation_id or "")
