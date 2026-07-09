from __future__ import annotations

from typing import cast

import pytest

from agentic_exception_sdk.budget.streaming import StreamingBudgetGuard
from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.taxonomy.errors import BudgetExhaustedError


class RaisingWatchdog:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.calls = 0

    def account_stream_delta(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros_usd: int = 0,
    ) -> None:
        self.calls += 1
        if self.fail_on is not None and self.calls >= self.fail_on:
            raise BudgetExhaustedError("budget exhausted")

    async def async_reserve_tokens(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros_usd: int = 0,
    ) -> None:
        self.account_stream_delta(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micros_usd=cost_micros_usd,
        )


def _watchdog(*, fail_on: int | None = None) -> BudgetWatchdog:
    return cast("BudgetWatchdog", RaisingWatchdog(fail_on=fail_on))


def test_guard_raises_on_budget_exceeded() -> None:
    guard = StreamingBudgetGuard(
        _watchdog(fail_on=3),
        tool_name="llm-call",
        agent_id="agent-1",
    )

    with pytest.raises(BudgetExhaustedError), guard:
        guard.record_chunk()
        guard.record_chunk()
        guard.record_chunk()

    assert guard.total_tokens_streamed == 2


def test_wrap_sync_stream_raises_on_budget() -> None:
    watchdog = _watchdog(fail_on=3)
    yielded: list[int] = []

    with pytest.raises(BudgetExhaustedError):
        for item in StreamingBudgetGuard.wrap_sync_stream(
            [1, 2, 3, 4],
            watchdog,
            tool_name="llm-call",
            agent_id="agent-1",
        ):
            yielded.append(item)

    assert yielded == [1, 2]


def test_guard_tracks_total_tokens() -> None:
    guard = StreamingBudgetGuard(
        _watchdog(),
        tool_name="llm-call",
        agent_id="agent-1",
    )

    guard.record_chunk(token_delta=2)
    guard.record_chunk(token_delta=3, cost_delta=0.25)

    assert guard.total_tokens_streamed == 5
    assert guard.total_cost_streamed == 0.25


@pytest.mark.asyncio
async def test_async_guard_raises_on_budget_exceeded() -> None:
    guard = StreamingBudgetGuard(
        _watchdog(fail_on=3),
        tool_name="llm-call",
        agent_id="agent-1",
    )

    with pytest.raises(BudgetExhaustedError):
        async with guard:
            await guard.arecord_chunk()
            await guard.arecord_chunk()
            await guard.arecord_chunk()

    assert guard.total_tokens_streamed == 2


def test_guard_passes_through_when_budget_ok() -> None:
    chunks = list(
        StreamingBudgetGuard.wrap_sync_stream(
            ["a", "b", "c"],
            _watchdog(),
            tool_name="llm-call",
            agent_id="agent-1",
        )
    )

    assert chunks == ["a", "b", "c"]
