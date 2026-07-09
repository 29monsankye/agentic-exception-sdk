from __future__ import annotations

from agentic_exception_sdk import NoOpResilienceBundle, ResilienceBundle, resilient
from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.resilience.circuit_breaker import InMemoryCircuitBreaker
from agentic_exception_sdk.resilience.retry import ExponentialBackoffRetry


def _bare_tool(value: int = 1) -> int:
    return value + 1


def _full_bundle() -> ResilienceBundle:
    return ResilienceBundle(
        retry_policy=ExponentialBackoffRetry(
            max_attempts=1,
            base_delay_seconds=0,
            jitter=False,
        ),
        circuit_breaker=InMemoryCircuitBreaker(),
        propagation_bus=InMemoryBus(max_size=10_000),
    )


def test_bare_call_overhead(benchmark) -> None:
    benchmark(_bare_tool)


def test_resilient_noop_bundle_overhead(benchmark) -> None:
    wrapped = resilient(
        NoOpResilienceBundle(),
        tool_name="benchmark-tool",
        agent_id="benchmark-agent",
    )(_bare_tool)
    benchmark(wrapped)


def test_resilient_full_in_memory_bundle_overhead(benchmark) -> None:
    wrapped = resilient(
        _full_bundle(),
        tool_name="benchmark-tool",
        agent_id="benchmark-agent",
    )(_bare_tool)
    benchmark(wrapped)
