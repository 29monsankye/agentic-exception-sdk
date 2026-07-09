"""Integration tests: async circuit breaker with async_resilient()."""

from __future__ import annotations

import asyncio

import pytest

from agentic_exception_sdk import (
    NoOpResilienceBundle,
    ResilienceBundle,
    async_resilient,
)
from agentic_exception_sdk.resilience.circuit_breaker import (
    AsyncInMemoryCircuitBreaker,
    CircuitState,
)

TOOL = "cb-tool"
AGENT = "cb-agent"


def make_bundle(**kwargs) -> ResilienceBundle:
    b = NoOpResilienceBundle()
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


class TestAsyncCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self):
        cb = AsyncInMemoryCircuitBreaker(failure_threshold=2, cooldown_seconds=999)
        bundle = make_bundle(async_circuit_breaker=cb)

        async def fail():
            raise ConnectionError("service down")

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT, fallback_value="fb")(
            fail
        )

        await wrapped()
        await wrapped()

        assert cb.state == CircuitState.OPEN

        result = await wrapped()
        assert result == "fb"

    @pytest.mark.asyncio
    async def test_circuit_recovers_after_cooldown(self):
        cb = AsyncInMemoryCircuitBreaker(
            failure_threshold=1,
            cooldown_seconds=0.0,
            half_open_probe_count=1,
        )
        bundle = make_bundle(async_circuit_breaker=cb)

        async def fail():
            raise ConnectionError("down")

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT, fallback_value="fb")(
            fail
        )
        await wrapped()  # trips the circuit

        assert cb.state == CircuitState.OPEN

        # Small sleep so cooldown elapses
        await asyncio.sleep(0.01)

        success_call_count = [0]

        async def succeed():
            success_call_count[0] += 1
            return "recovered"

        wrapped_success = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT)(succeed)
        result = await wrapped_success()
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_concurrent_async_calls_with_open_circuit(self):
        cb = AsyncInMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=999)
        bundle = make_bundle(async_circuit_breaker=cb)

        async def fail():
            raise ConnectionError("down")

        wrapped = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT, fallback_value="fb")(
            fail
        )
        await wrapped()  # trip the circuit

        results = await asyncio.gather(*[wrapped() for _ in range(5)])
        assert all(r == "fb" for r in results)

    @pytest.mark.asyncio
    async def test_state_transitions_counted(self):
        cb = AsyncInMemoryCircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        bundle = make_bundle(async_circuit_breaker=cb)

        async def fail():
            raise ConnectionError("down")

        wrapped_fail = async_resilient(
            bundle, tool_name=TOOL, agent_id=AGENT, fallback_value="fb"
        )(fail)
        await wrapped_fail()  # CLOSED -> OPEN

        await asyncio.sleep(0.01)

        async def succeed():
            return "ok"

        wrapped_ok = async_resilient(bundle, tool_name=TOOL, agent_id=AGENT)(succeed)
        await wrapped_ok()  # OPEN -> HALF_OPEN -> CLOSED

        assert cb.state_transition_total >= 2
