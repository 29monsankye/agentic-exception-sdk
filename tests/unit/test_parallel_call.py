from __future__ import annotations

import pytest

from agentic_exception_sdk import (
    AgentExceptionClass,
    AgentHardKillError,
    InMemoryBus,
    PromptInjectionError,
    ResilienceBundle,
    call_parallel,
)


def _bundle() -> ResilienceBundle:
    return ResilienceBundle(propagation_bus=InMemoryBus())


@pytest.mark.asyncio
async def test_call_parallel_all_success() -> None:
    results, envelope = await call_parallel(
        [
            (_bundle(), "tool-a", "agent-a", lambda: "a"),
            (_bundle(), "tool-b", "agent-b", lambda: "b"),
        ],
        correlation_id="corr-1",
    )

    assert results == ["a", "b"]
    assert envelope.aggregate_class is None
    assert envelope.success_count == 2
    assert envelope.failure_count == 0
    assert envelope.summary == {}
    assert [outcome.status for outcome in envelope.outcomes] == ["success", "success"]


@pytest.mark.asyncio
async def test_call_parallel_mixed_outcomes_aggregate_most_severe() -> None:
    def transient_failure() -> None:
        raise TimeoutError("upstream unavailable")

    def issue_failure() -> None:
        raise PermissionError("missing checkpoint")

    results, envelope = await call_parallel(
        [
            (_bundle(), "tool-a", "agent-a", lambda: "a"),
            (_bundle(), "tool-b", "agent-b", transient_failure),
            (_bundle(), "tool-c", "agent-c", issue_failure),
        ],
        correlation_id="corr-2",
    )

    assert results == ["a", None, None]
    assert envelope.success_count == 1
    assert envelope.failure_count == 2
    assert envelope.aggregate_class == AgentExceptionClass.ISSUE
    assert envelope.summary == {"exception": 1, "issue": 1}
    assert [outcome.tool_name for outcome in envelope.outcomes] == [
        "tool-a",
        "tool-b",
        "tool-c",
    ]


@pytest.mark.asyncio
async def test_call_parallel_hard_kill_propagates() -> None:
    def hard_kill() -> None:
        raise PromptInjectionError("prompt injection detected")

    with pytest.raises(AgentHardKillError):
        await call_parallel(
            [
                (_bundle(), "tool-a", "agent-a", lambda: "a"),
                (_bundle(), "tool-b", "agent-b", hard_kill),
            ],
            correlation_id="corr-3",
        )


@pytest.mark.asyncio
async def test_call_parallel_preserves_input_order_for_async_calls() -> None:
    async def async_success() -> str:
        return "async"

    def transient_failure() -> None:
        raise TimeoutError("upstream unavailable")

    results, envelope = await call_parallel(
        [
            (_bundle(), "tool-a", "agent-a", async_success),
            (_bundle(), "tool-b", "agent-b", transient_failure),
            (_bundle(), "tool-c", "agent-c", lambda: "c"),
        ],
        correlation_id="corr-4",
    )

    assert results == ["async", None, "c"]
    assert [outcome.agent_id for outcome in envelope.outcomes] == [
        "agent-a",
        "agent-b",
        "agent-c",
    ]
    assert envelope.summary == {"exception": 1}
