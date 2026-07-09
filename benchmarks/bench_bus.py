from __future__ import annotations

from datetime import UTC, datetime

from agentic_exception_sdk.propagation.bus import InMemoryBus
from agentic_exception_sdk.taxonomy.enums import (
    AgentExceptionClass,
    EscalationLevel,
    ExceptionSource,
)
from agentic_exception_sdk.taxonomy.envelope import AgentExceptionEnvelope, SafeContextSnapshot


def _envelope(index: int) -> AgentExceptionEnvelope:
    return AgentExceptionEnvelope(
        exception_id=f"00000000-0000-0000-0000-{index:012d}",
        agent_id="benchmark-agent",
        tool_name="benchmark-tool",
        exception_class=AgentExceptionClass.EXCEPTION,
        source=ExceptionSource.TOOL,
        error_type="TimeoutError",
        message="timeout",
        context_snapshot=SafeContextSnapshot({}),
        suggested_recovery=EscalationLevel.L0_SELF_RETRY,
        occurred_at=datetime.now(UTC),
    )


def test_in_memory_bus_publish_throughput(benchmark) -> None:
    envelopes = [_envelope(index) for index in range(1_000)]

    def publish_batch() -> int:
        bus = InMemoryBus(max_size=2_000)
        for envelope in envelopes:
            bus.publish(envelope)
        return bus.size

    assert benchmark(publish_batch) == 1_000
