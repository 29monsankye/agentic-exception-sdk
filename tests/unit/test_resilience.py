"""Tests for resilience components: retry, circuit breaker, fallback, compensating."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from agentic_exception_sdk.resilience.circuit_breaker import (
    AsyncInMemoryCircuitBreaker,
    CircuitState,
    InMemoryCircuitBreaker,
    RedisCircuitBreaker,
)
from agentic_exception_sdk.resilience.compensating import CompensatingTransactionRegistry
from agentic_exception_sdk.resilience.fallback import NoOpFallback, OrderedFallbackChain
from agentic_exception_sdk.resilience.retry import (
    ExponentialBackoffRetry,
    InMemoryRetryInFlightTracker,
    NoOpRetry,
    RetryContext,
    RetryInFlightEntry,
)
from agentic_exception_sdk.taxonomy.errors import (
    CircuitBreakerStateUnavailableError,
    CompensationPartialFailureError,
)

# ---------------------------------------------------------------------------
# NoOpRetry tests
# ---------------------------------------------------------------------------

class TestNoOpRetry:
    def test_executes_once(self):
        calls = []
        retry = NoOpRetry()
        retry.execute(lambda: calls.append(1))
        assert len(calls) == 1

    def test_reraises_on_failure(self):
        retry = NoOpRetry()
        with pytest.raises(ValueError):
            retry.execute(lambda: (_ for _ in ()).throw(ValueError("fail")))

    @pytest.mark.asyncio
    async def test_async_executes_once(self):
        calls = []
        async def fn():
            calls.append(1)
            return 42
        result = await NoOpRetry().async_execute(fn)
        assert result == 42
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# ExponentialBackoffRetry tests
# ---------------------------------------------------------------------------

class TestExponentialBackoffRetry:
    def test_succeeds_on_first_attempt(self):
        retry = ExponentialBackoffRetry(max_attempts=3, retryable_exceptions=(TimeoutError,))
        result = retry.execute(lambda: 99)
        assert result == 99

    def test_retries_on_retryable_exception(self):
        calls = [0]
        def fn():
            calls[0] += 1
            if calls[0] < 3:
                raise TimeoutError("retry me")
            return "done"

        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        result = retry.execute(fn)
        assert result == "done"
        assert calls[0] == 3

    def test_exhausted_raises_last_exception(self):
        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        with pytest.raises(TimeoutError, match="permanent"):
            retry.execute(lambda: (_ for _ in ()).throw(TimeoutError("permanent")))

    def test_non_retryable_not_retried(self):
        calls = [0]
        def fn():
            calls[0] += 1
            raise ValueError("not retryable")

        retry = ExponentialBackoffRetry(
            max_attempts=3,
            retryable_exceptions=(TimeoutError,),
        )
        with pytest.raises(ValueError):
            retry.execute(fn)
        assert calls[0] == 1

    def test_idempotency_key_fn_called_on_retry(self):
        keys_seen = []
        def key_fn(ctx: RetryContext) -> str:
            keys_seen.append(ctx.attempt)
            return f"key-{ctx.attempt}"

        calls = [0]
        def fn():
            calls[0] += 1
            if calls[0] < 2:
                raise TimeoutError("retry")
            return "ok"

        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
            idempotency_key_fn=key_fn,
        )
        retry.execute(fn)
        assert 2 in keys_seen  # idempotency_key_fn called on attempt 2+

    def test_retry_after_header_delta_respected(self):
        class FakeHTTPError(TimeoutError):
            class response:
                class headers:
                    @staticmethod
                    def get(key):
                        return "1"  # 1 second

        delays = []

        def mock_sleep(s):
            delays.append(s)

        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
            max_retry_after_seconds=60,
        )
        calls = [0]
        def fn():
            calls[0] += 1
            if calls[0] == 1:
                raise FakeHTTPError()
            return "ok"

        with patch("time.sleep", mock_sleep):
            retry.execute(fn)

        assert len(delays) == 1
        assert delays[0] >= 1.0

    def test_inflight_tracker_records_before_sleep_and_completes_on_success(self):
        tracker = InMemoryRetryInFlightTracker()
        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
            inflight_tracker=tracker,
        )
        calls = [0]
        active_during_sleep = []

        def fn():
            calls[0] += 1
            if calls[0] == 1:
                raise TimeoutError("retry")
            return "ok"

        def mock_sleep(delay):
            active_during_sleep.extend(tracker.list_active())

        with patch("time.sleep", mock_sleep):
            assert retry.execute(
                fn,
                context=RetryContext(
                    correlation_id="corr-retry",
                    agent_id="agent-a",
                    tool_name="tool-a",
                ),
            ) == "ok"

        assert len(active_during_sleep) == 1
        entry = active_during_sleep[0]
        assert entry.correlation_id == "corr-retry"
        assert entry.agent_id == "agent-a"
        assert entry.tool_name == "tool-a"
        assert entry.attempt == 1
        assert entry.max_attempts == 2
        assert entry.error_type == "TimeoutError"
        assert entry.next_retry_at is not None
        assert tracker.list_active() == []

    def test_inflight_tracker_completes_on_final_exhaustion(self):
        tracker = InMemoryRetryInFlightTracker()
        retry = ExponentialBackoffRetry(
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
            inflight_tracker=tracker,
        )

        with pytest.raises(TimeoutError), patch("time.sleep", lambda delay: None):
            retry.execute(
                lambda: (_ for _ in ()).throw(TimeoutError("permanent")),
                context=RetryContext(correlation_id="corr-exhausted"),
            )

        assert tracker.list_active() == []

    def test_in_memory_inflight_tracker_returns_snapshot(self):
        tracker = InMemoryRetryInFlightTracker()
        entry = RetryInFlightEntry(
            correlation_id="corr-a",
            agent_id="agent-a",
            tool_name="tool-a",
            attempt=1,
            max_attempts=3,
            error_type="TimeoutError",
            started_at=datetime.now(UTC),
            next_retry_at=None,
        )

        tracker.record_attempt(entry)
        snapshot = tracker.list_active()
        snapshot.clear()

        assert tracker.list_active() == [entry]

    @pytest.mark.asyncio
    async def test_async_retries(self):
        calls = [0]
        async def fn():
            calls[0] += 1
            if calls[0] < 2:
                raise TimeoutError("async retry")
            return "async-done"

        retry = ExponentialBackoffRetry(
            max_attempts=3,
            base_delay_seconds=0.0,
            jitter=False,
            retryable_exceptions=(TimeoutError,),
        )
        result = await retry.async_execute(fn)
        assert result == "async-done"
        assert calls[0] == 2


# ---------------------------------------------------------------------------
# InMemoryCircuitBreaker tests
# ---------------------------------------------------------------------------

class TestInMemoryCircuitBreaker:
    def test_closed_passes_through(self):
        cb = InMemoryCircuitBreaker()
        assert cb.call(lambda: 42) == 42
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_failure_threshold(self):
        cb = InMemoryCircuitBreaker(failure_threshold=3, cooldown_seconds=999)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_calls(self):
        cb = InMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=999)
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerStateUnavailableError):
            cb.call(lambda: 1)

    def test_half_open_after_cooldown(self):
        cb = InMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        time.sleep(0.01)
        result = cb.call(lambda: "probe-ok")
        assert result == "probe-ok"
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = InMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        time.sleep(0.01)
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("probe fail")))
        assert cb.state == CircuitState.OPEN

    def test_state_transition_counter(self):
        cb = InMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        assert cb.state_transition_total >= 1

    def test_thread_safe(self):
        cb = InMemoryCircuitBreaker(failure_threshold=100)
        results = []
        def worker():
            for _ in range(10):
                try:
                    cb.call(lambda: results.append(1))
                except CircuitBreakerStateUnavailableError:
                    pass
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) <= 500


class TestRedisCircuitBreaker:
    def test_requires_tls_url(self):
        with pytest.raises(ValueError, match="rediss"):
            RedisCircuitBreaker(redis_url="redis://:pw@example.com:6379/0")

    def test_requires_auth_credentials(self):
        with pytest.raises(ValueError, match="AUTH"):
            RedisCircuitBreaker(redis_url="rediss://example.com:6379/0")

    def test_namespaced_key_and_state_machine_with_fake_client(self):
        class FakeRedis:
            def __init__(self):
                self.data = {}

            def hgetall(self, key):
                return self.data.get(key, {})

            def hset(self, key, *, mapping):
                self.data[key] = dict(mapping)

        fake = FakeRedis()
        cb = RedisCircuitBreaker(
            redis_url="rediss://user:pw@example.com:6379/0",
            redis_client=fake,
            failure_threshold=1,
            cooldown_seconds=999,
            circuit_name="payments",
            environment="test",
        )

        assert cb.key == "agentic-exception-sdk:1.1.0:test:circuit-breaker:payments"
        with pytest.raises(RuntimeError):
            cb.call(lambda: (_ for _ in ()).throw(RuntimeError("down")))
        with pytest.raises(CircuitBreakerStateUnavailableError, match="OPEN"):
            cb.call(lambda: "blocked")

    def test_redis_state_unavailable_fails_closed(self):
        class BrokenRedis:
            def hgetall(self, key):
                raise OSError("redis down")

        cb = RedisCircuitBreaker(
            redis_url="rediss://user:pw@example.com:6379/0",
            redis_client=BrokenRedis(),
            state_unavailable_retry_budget=0,
        )
        with pytest.raises(CircuitBreakerStateUnavailableError, match="tier=issue"):
            cb.call(lambda: "blocked")


# ---------------------------------------------------------------------------
# AsyncInMemoryCircuitBreaker tests
# ---------------------------------------------------------------------------

class TestAsyncInMemoryCircuitBreaker:
    @pytest.mark.asyncio
    async def test_closed_passes_through(self):
        cb = AsyncInMemoryCircuitBreaker()
        async def fn():
            return 99
        assert await cb.call(fn) == 99

    @pytest.mark.asyncio
    async def test_opens_after_failures(self):
        cb = AsyncInMemoryCircuitBreaker(failure_threshold=2, cooldown_seconds=999)
        async def fail():
            raise RuntimeError("async fail")
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_rejects_calls(self):
        cb = AsyncInMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=999)
        async def fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError):
            await cb.call(fail)
        with pytest.raises(CircuitBreakerStateUnavailableError):
            await cb.call(lambda: asyncio.sleep(0))


# ---------------------------------------------------------------------------
# OrderedFallbackChain tests
# ---------------------------------------------------------------------------

class TestOrderedFallbackChain:
    def test_first_success_returned(self):
        chain = OrderedFallbackChain([lambda: 1, lambda: 2])
        assert chain.execute() == 1

    def test_second_tried_on_first_failure(self):
        chain = OrderedFallbackChain([
            lambda: (_ for _ in ()).throw(ValueError("a")),
            lambda: "fallback",
        ])
        assert chain.execute() == "fallback"

    def test_all_fail_reraises_last(self):
        chain = OrderedFallbackChain([
            lambda: (_ for _ in ()).throw(ValueError("a")),
            lambda: (_ for _ in ()).throw(ValueError("b")),
        ])
        with pytest.raises(ValueError, match="b"):
            chain.execute()

    def test_noop_returns_none(self):
        assert NoOpFallback().execute() is None


# ---------------------------------------------------------------------------
# CompensatingTransactionRegistry tests
# ---------------------------------------------------------------------------

class TestCompensatingTransactionRegistry:
    def test_compensators_run_lifo(self):
        order = []
        reg = CompensatingTransactionRegistry()
        reg.register(correlation_id="r1", step_id="a", compensate=lambda: order.append("a"))
        reg.register(correlation_id="r1", step_id="b", compensate=lambda: order.append("b"))
        reg.compensate("r1")
        assert order == ["b", "a"]

    def test_all_compensators_attempted_on_partial_failure(self):
        ran = []
        reg = CompensatingTransactionRegistry()
        reg.register(correlation_id="r1", step_id="a", compensate=lambda: ran.append("a"))
        reg.register(
            correlation_id="r1",
            step_id="fail",
            compensate=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        reg.register(correlation_id="r1", step_id="c", compensate=lambda: ran.append("c"))
        with pytest.raises(CompensationPartialFailureError):
            reg.compensate("r1")
        assert "a" in ran  # later steps still ran
        assert "c" in ran

    def test_compensation_partial_failure_is_base_exception(self):
        assert issubclass(CompensationPartialFailureError, BaseException)

    def test_clear_removes_compensators(self):
        ran = []
        reg = CompensatingTransactionRegistry()
        reg.register(correlation_id="r1", step_id="a", compensate=lambda: ran.append("a"))
        reg.clear("r1")
        reg.compensate("r1")
        assert ran == []
