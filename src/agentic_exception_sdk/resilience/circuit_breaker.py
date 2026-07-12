"""Circuit breaker protocol and implementations.

Sync circuit breaker uses threading.RLock for all state transitions.
Async circuit breaker uses asyncio.Lock — never acquires a blocking thread lock
from the event loop. Do not build a unified sync/async lock abstraction.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar, runtime_checkable
from urllib.parse import urlsplit

from agentic_exception_sdk.taxonomy.errors import (
    BudgetExhaustedError,
    CircuitBreakerStateUnavailableError,
)

_log = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "AsyncCircuitBreaker",
    "AsyncInMemoryCircuitBreaker",
    "CircuitBreaker",
    "CircuitState",
    "InMemoryCircuitBreaker",
    "NoOpAsyncCircuitBreaker",
    "NoOpCircuitBreaker",
    "RedisCircuitBreaker",
]


class CircuitState(Enum):
    """State of a circuit breaker."""

    CLOSED = "closed"
    """Passing calls through; counting failures."""

    OPEN = "open"
    """Rejecting calls; waiting for cooldown period."""

    HALF_OPEN = "half_open"
    """Allowing probe calls to test if the service has recovered."""


@runtime_checkable
class CircuitBreaker(Protocol):
    """Protocol for synchronous circuit breakers."""

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn through the circuit breaker.

        Args:
            fn: Zero-argument callable to execute.

        Returns:
            The return value of fn.

        Raises:
            CircuitBreakerStateUnavailableError: When the circuit is OPEN.
        """
        ...


@runtime_checkable
class AsyncCircuitBreaker(Protocol):
    """Protocol for asynchronous circuit breakers."""

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute async fn through the async circuit breaker.

        Args:
            fn: Zero-argument async callable to execute.

        Returns:
            The awaited return value of fn.

        Raises:
            CircuitBreakerStateUnavailableError: When the circuit is OPEN.
        """
        ...


class NoOpCircuitBreaker:
    """Circuit breaker that always passes calls through (no-op)."""

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn without circuit-breaker logic.

        Args:
            fn: Zero-argument callable to execute.

        Returns:
            The return value of fn.
        """
        return fn()

    async def async_call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute an async function without circuit-breaker logic."""
        return await fn()


class NoOpAsyncCircuitBreaker:
    """Async circuit breaker that always passes calls through (no-op)."""

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute async fn without circuit-breaker logic.

        Args:
            fn: Zero-argument async callable to execute.

        Returns:
            The awaited return value of fn.
        """
        return await fn()


class InMemoryCircuitBreaker:
    """Thread-safe synchronous circuit breaker with closed/open/half-open state machine.

    All state transitions and failure counters are protected by threading.RLock.
    This breaker must not be called from an async event loop — use
    AsyncInMemoryCircuitBreaker for async code.

    Args:
        failure_threshold: Number of failures before transitioning to OPEN. Default 5.
        cooldown_seconds: Time in OPEN state before attempting HALF_OPEN. Default 30.
        half_open_probe_count: Successful probes needed to transition back to CLOSED. Default 1.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        half_open_probe_count: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_probe_count = half_open_probe_count

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._probe_success_count = 0
        self._probe_in_flight_count = 0
        self._opened_at: float | None = None
        self._lock = threading.RLock()
        self.state_transition_total: int = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state (thread-safe read)."""
        with self._lock:
            return self._state

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state and increment the counter. Must hold the lock."""
        self._state = new_state
        self.state_transition_total += 1
        _log.debug("circuit breaker transition -> %s", new_state.value)

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn through the sync circuit breaker.

        Args:
            fn: Zero-argument callable to execute.

        Returns:
            The return value of fn.

        Raises:
            CircuitBreakerStateUnavailableError: When the circuit is OPEN.
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if cooldown has elapsed
                if (
                    self._opened_at is not None
                    and time.monotonic() - self._opened_at >= self._cooldown_seconds
                ):
                    self._probe_success_count = 0
                    self._probe_in_flight_count = 0
                    self._transition_to(CircuitState.HALF_OPEN)
                else:
                    raise CircuitBreakerStateUnavailableError("circuit breaker is OPEN")
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight_count >= self._half_open_probe_count:
                    raise CircuitBreakerStateUnavailableError(
                        "circuit breaker is HALF_OPEN and probe budget is exhausted"
                    )
                self._probe_in_flight_count += 1
                admitted_probe = True
            else:
                admitted_probe = False

        try:
            result = fn()
        except BudgetExhaustedError:
            # Budget exhaustion is an internal control signal, not a downstream
            # fault. It must not trip the breaker or consume its failure budget;
            # just release any probe slot we admitted and propagate.
            with self._lock:
                if admitted_probe:
                    self._probe_in_flight_count = max(0, self._probe_in_flight_count - 1)
            raise
        except Exception:
            with self._lock:
                if self._state == CircuitState.HALF_OPEN:
                    self._transition_to(CircuitState.OPEN)
                    self._opened_at = time.monotonic()
                    self._failure_count = 0
                    self._probe_success_count = 0
                if admitted_probe:
                    self._probe_in_flight_count = max(
                        0,
                        self._probe_in_flight_count - 1,
                    )
                else:
                    self._failure_count += 1
                    if self._failure_count >= self._failure_threshold:
                        self._transition_to(CircuitState.OPEN)
                        self._opened_at = time.monotonic()
                        self._failure_count = 0
            raise

        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                if admitted_probe:
                    self._probe_in_flight_count = max(
                        0,
                        self._probe_in_flight_count - 1,
                    )
                self._probe_success_count += 1
                if self._probe_success_count >= self._half_open_probe_count:
                    self._failure_count = 0
                    self._probe_success_count = 0
                    self._probe_in_flight_count = 0
                    self._transition_to(CircuitState.CLOSED)
            else:
                # Successful call in CLOSED state resets failure counter
                self._failure_count = 0

        return result

    async def async_call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute an async function through the thread-safe state machine."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if (
                    self._opened_at is not None
                    and time.monotonic() - self._opened_at >= self._cooldown_seconds
                ):
                    self._probe_success_count = 0
                    self._probe_in_flight_count = 0
                    self._transition_to(CircuitState.HALF_OPEN)
                else:
                    raise CircuitBreakerStateUnavailableError("circuit breaker is OPEN")
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight_count >= self._half_open_probe_count:
                    raise CircuitBreakerStateUnavailableError(
                        "circuit breaker is HALF_OPEN and probe budget is exhausted"
                    )
                self._probe_in_flight_count += 1
                admitted_probe = True
            else:
                admitted_probe = False

        try:
            result = await fn()
        except BudgetExhaustedError:
            # Budget exhaustion is an internal control signal, not a downstream
            # fault. It must not trip the breaker or consume its failure budget;
            # just release any probe slot we admitted and propagate.
            with self._lock:
                if admitted_probe:
                    self._probe_in_flight_count = max(0, self._probe_in_flight_count - 1)
            raise
        except Exception:
            with self._lock:
                if self._state == CircuitState.HALF_OPEN:
                    self._transition_to(CircuitState.OPEN)
                    self._opened_at = time.monotonic()
                    self._failure_count = 0
                    self._probe_success_count = 0
                if admitted_probe:
                    self._probe_in_flight_count = max(0, self._probe_in_flight_count - 1)
                else:
                    self._failure_count += 1
                    if self._failure_count >= self._failure_threshold:
                        self._transition_to(CircuitState.OPEN)
                        self._opened_at = time.monotonic()
                        self._failure_count = 0
            raise

        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                if admitted_probe:
                    self._probe_in_flight_count = max(0, self._probe_in_flight_count - 1)
                self._probe_success_count += 1
                if self._probe_success_count >= self._half_open_probe_count:
                    self._failure_count = 0
                    self._probe_success_count = 0
                    self._probe_in_flight_count = 0
                    self._transition_to(CircuitState.CLOSED)
            else:
                self._failure_count = 0
        return result


class AsyncInMemoryCircuitBreaker:
    """Async circuit breaker using asyncio.Lock — never blocks the event loop.

    Provides the same state machine as InMemoryCircuitBreaker but safe for use
    inside async coroutines. Must not use threading.RLock.

    Args:
        failure_threshold: Number of failures before transitioning to OPEN. Default 5.
        cooldown_seconds: Time in OPEN state before attempting HALF_OPEN. Default 30.
        half_open_probe_count: Successful probes needed to transition back to CLOSED. Default 1.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        half_open_probe_count: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_probe_count = half_open_probe_count

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._probe_success_count = 0
        self._probe_in_flight_count = 0
        self._opened_at: float | None = None
        self._lock: asyncio.Lock | None = None
        self.state_transition_total: int = 0

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def state(self) -> CircuitState:
        """Current circuit state (snapshot — not guaranteed under contention)."""
        return self._state

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state. Must be called while holding the lock."""
        self._state = new_state
        self.state_transition_total += 1
        _log.debug("async circuit breaker transition -> %s", new_state.value)

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute async fn through the async circuit breaker.

        Args:
            fn: Zero-argument async callable to execute.

        Returns:
            The awaited return value of fn.

        Raises:
            CircuitBreakerStateUnavailableError: When the circuit is OPEN.
        """
        lock = self._get_lock()

        async with lock:
            if self._state == CircuitState.OPEN:
                if (
                    self._opened_at is not None
                    and time.monotonic() - self._opened_at >= self._cooldown_seconds
                ):
                    self._probe_success_count = 0
                    self._probe_in_flight_count = 0
                    self._transition_to(CircuitState.HALF_OPEN)
                else:
                    raise CircuitBreakerStateUnavailableError("async circuit breaker is OPEN")
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight_count >= self._half_open_probe_count:
                    raise CircuitBreakerStateUnavailableError(
                        "async circuit breaker is HALF_OPEN and probe budget is exhausted"
                    )
                self._probe_in_flight_count += 1
                admitted_probe = True
            else:
                admitted_probe = False

        try:
            result = await fn()
        except Exception:
            async with lock:
                if self._state == CircuitState.HALF_OPEN:
                    self._transition_to(CircuitState.OPEN)
                    self._opened_at = time.monotonic()
                    self._failure_count = 0
                    self._probe_success_count = 0
                if admitted_probe:
                    self._probe_in_flight_count = max(
                        0,
                        self._probe_in_flight_count - 1,
                    )
                else:
                    self._failure_count += 1
                    if self._failure_count >= self._failure_threshold:
                        self._transition_to(CircuitState.OPEN)
                        self._opened_at = time.monotonic()
                        self._failure_count = 0
            raise

        async with lock:
            if self._state == CircuitState.HALF_OPEN:
                if admitted_probe:
                    self._probe_in_flight_count = max(
                        0,
                        self._probe_in_flight_count - 1,
                    )
                self._probe_success_count += 1
                if self._probe_success_count >= self._half_open_probe_count:
                    self._failure_count = 0
                    self._probe_success_count = 0
                    self._probe_in_flight_count = 0
                    self._transition_to(CircuitState.CLOSED)
            else:
                self._failure_count = 0

        return result


@dataclass(frozen=True)
class _RedisState:
    state: CircuitState
    failure_count: int
    probe_success_count: int
    opened_at: float | None


class RedisCircuitBreaker:
    """Redis-backed distributed circuit breaker.

    Requires the ``redis`` extra: ``pip install agentic-exception-sdk[redis]``.
    Full implementation requires the agentic-exception-iac circuit_breaker_state
    Terraform module. Redis URL must use ``rediss://`` (TLS), AUTH/ACLs, and keys
    namespaced by SDK version, environment, and logical circuit name.

    Fails closed (raises CircuitBreakerStateUnavailableError) when Redis state
    is unavailable. State outage classification is two-tiered to avoid paging
    on every request during an outage.

    Args:
        redis_url: Redis URL (must start with ``rediss://``).
        failure_threshold: Failures before opening circuit.
        cooldown_seconds: Time before HALF_OPEN probe.
        half_open_probe_count: Probe successes needed to close.
        state_unavailable_retry_budget: EXCEPTION-tier budget before escalating to ISSUE.

    Raises:
        ValueError: If redis_url is not TLS-protected or lacks AUTH/ACL credentials.
        ImportError: If the redis optional extra is not installed.
    """

    def __init__(
        self,
        *,
        redis_url: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        half_open_probe_count: int = 1,
        state_unavailable_retry_budget: int = 3,
        circuit_name: str = "default",
        environment: str = "dev",
        sdk_version: str = "1.1.0",
        redis_client: object | None = None,
    ) -> None:
        parsed = urlsplit(redis_url)
        if parsed.scheme != "rediss":
            raise ValueError("RedisCircuitBreaker requires rediss:// TLS Redis URLs")
        if not parsed.password:
            raise ValueError("RedisCircuitBreaker requires Redis AUTH/ACL credentials")

        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_probe_count = half_open_probe_count
        self._state_unavailable_retry_budget = state_unavailable_retry_budget
        self._state_unavailable_count = 0
        self._lock = threading.RLock()
        self.state_transition_total = 0
        self._key = (
            f"agentic-exception-sdk:{sdk_version}:{environment}:circuit-breaker:{circuit_name}"
        )

        if redis_client is not None:
            self._redis = redis_client
        else:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise ImportError(
                    "RedisCircuitBreaker requires 'pip install agentic-exception-sdk[redis]'"
                ) from exc
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True)

    @property
    def key(self) -> str:
        """Namespaced Redis key used for circuit-breaker state."""
        return self._key

    @property
    def state_unavailable_count(self) -> int:
        """Number of Redis state failures observed by this process."""
        with self._lock:
            return self._state_unavailable_count

    def _fail_closed(self, exc: BaseException) -> CircuitBreakerStateUnavailableError:
        with self._lock:
            self._state_unavailable_count += 1
            tier = (
                "issue"
                if self._state_unavailable_count > self._state_unavailable_retry_budget
                else "exception"
            )
        return CircuitBreakerStateUnavailableError(
            "redis circuit-breaker state unavailable "
            f"(tier={tier}, failures={self._state_unavailable_count}): {exc}"
        )

    @staticmethod
    def _decode_mapping(raw: object) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        decoded: dict[str, str] = {}
        for key, value in raw.items():
            k = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            v = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            decoded[k] = v
        return decoded

    def _load_state(self) -> _RedisState:
        try:
            raw = self._decode_mapping(self._redis.hgetall(self._key))  # type: ignore[attr-defined]
        except Exception as exc:
            raise self._fail_closed(exc) from exc

        if not raw:
            return _RedisState(CircuitState.CLOSED, 0, 0, None)

        try:
            state = CircuitState(raw.get("state", CircuitState.CLOSED.value))
            opened_at_raw = raw.get("opened_at")
            opened_at = float(opened_at_raw) if opened_at_raw else None
            return _RedisState(
                state=state,
                failure_count=int(raw.get("failure_count", "0")),
                probe_success_count=int(raw.get("probe_success_count", "0")),
                opened_at=opened_at,
            )
        except (TypeError, ValueError) as exc:
            raise self._fail_closed(exc) from exc

    def _save_state(self, state: _RedisState) -> None:
        mapping = {
            "state": state.state.value,
            "failure_count": str(state.failure_count),
            "probe_success_count": str(state.probe_success_count),
            "opened_at": "" if state.opened_at is None else str(state.opened_at),
        }
        try:
            self._redis.hset(self._key, mapping=mapping)  # type: ignore[attr-defined]
        except Exception as exc:
            raise self._fail_closed(exc) from exc

    def _transition_to(self, state: CircuitState) -> None:
        self.state_transition_total += 1
        _log.debug("redis circuit breaker transition -> %s", state.value)

    def _before_call(self) -> None:
        state = self._load_state()
        if state.state == CircuitState.OPEN:
            if (
                state.opened_at is not None
                and time.monotonic() - state.opened_at >= self._cooldown_seconds
            ):
                self._transition_to(CircuitState.HALF_OPEN)
                self._save_state(_RedisState(CircuitState.HALF_OPEN, 0, 0, state.opened_at))
                return
            raise CircuitBreakerStateUnavailableError("redis circuit breaker is OPEN")

    def _record_failure(self) -> None:
        state = self._load_state()
        if state.state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
            self._save_state(_RedisState(CircuitState.OPEN, 0, 0, time.monotonic()))
            return

        failure_count = state.failure_count + 1
        if failure_count >= self._failure_threshold:
            self._transition_to(CircuitState.OPEN)
            self._save_state(_RedisState(CircuitState.OPEN, 0, 0, time.monotonic()))
        else:
            self._save_state(
                _RedisState(state.state, failure_count, state.probe_success_count, state.opened_at)
            )

    def _record_success(self) -> None:
        state = self._load_state()
        if state.state == CircuitState.HALF_OPEN:
            probe_success_count = state.probe_success_count + 1
            if probe_success_count >= self._half_open_probe_count:
                self._transition_to(CircuitState.CLOSED)
                self._save_state(_RedisState(CircuitState.CLOSED, 0, 0, None))
            else:
                self._save_state(
                    _RedisState(CircuitState.HALF_OPEN, 0, probe_success_count, state.opened_at)
                )
        else:
            self._save_state(_RedisState(CircuitState.CLOSED, 0, 0, None))

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn through Redis-backed circuit state."""
        self._before_call()
        try:
            result = fn()
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    async def async_call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute async fn through Redis-backed circuit state."""
        await asyncio.to_thread(self._before_call)
        try:
            result = await fn()
        except Exception:
            await asyncio.to_thread(self._record_failure)
            raise
        await asyncio.to_thread(self._record_success)
        return result
