"""Streaming LLM budget enforcement.

Provides a context manager and async generator wrapper that checks the
BudgetWatchdog after each streamed chunk. When the budget ceiling is exceeded
mid-stream, raises BudgetExhaustedError to abort generation.

Usage (OpenAI-style streaming):
    guard = StreamingBudgetGuard(watchdog, tool_name="llm-call", agent_id="agent-1")
    with guard:
        for chunk in openai_client.chat.completions.create(..., stream=True):
            guard.record_chunk(token_delta=chunk.usage.completion_tokens or 1)
            yield chunk.choices[0].delta.content

Usage (async):
    async with guard:
        async for chunk in async_stream:
            await guard.arecord_chunk(token_delta=...)
            yield chunk
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Callable, Generator, Iterable
from typing import Literal, TypeVar

from agentic_exception_sdk.budget.watchdog import BudgetWatchdog
from agentic_exception_sdk.taxonomy.errors import BudgetExhaustedError

__all__ = ["StreamingBudgetGuard"]

T = TypeVar("T")


class StreamingBudgetGuard:
    """Abort streaming LLM output when a BudgetWatchdog ceiling is exceeded."""

    def __init__(
        self,
        watchdog: BudgetWatchdog,
        *,
        tool_name: str,
        agent_id: str,
        correlation_id: str | None = None,
    ) -> None:
        self.watchdog = watchdog
        self.tool_name = tool_name
        self.agent_id = agent_id
        self.correlation_id = correlation_id
        self.total_tokens_streamed = 0
        self.total_cost_streamed = 0.0

    def __enter__(self) -> StreamingBudgetGuard:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        return False

    async def __aenter__(self) -> StreamingBudgetGuard:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        return False

    def record_chunk(self, token_delta: int = 1, cost_delta: float = 0.0) -> None:
        """Record one streamed chunk and raise immediately on budget exhaustion."""
        cost_micros_usd = _cost_to_micros(cost_delta)
        self.watchdog.account_stream_delta(
            output_tokens=token_delta,
            cost_micros_usd=cost_micros_usd,
        )
        self.total_tokens_streamed += token_delta
        self.total_cost_streamed += cost_delta

    async def arecord_chunk(self, token_delta: int = 1, cost_delta: float = 0.0) -> None:
        """Async record of one streamed chunk and raise on budget exhaustion."""
        cost_micros_usd = _cost_to_micros(cost_delta)
        await self.watchdog.async_reserve_tokens(
            output_tokens=token_delta,
            cost_micros_usd=cost_micros_usd,
        )
        self.total_tokens_streamed += token_delta
        self.total_cost_streamed += cost_delta

    @classmethod
    def wrap_sync_stream(
        cls,
        stream: Iterable[T],
        watchdog: BudgetWatchdog,
        *,
        tool_name: str,
        agent_id: str,
        token_extractor: Callable[[T], int] = lambda _: 1,
        cost_extractor: Callable[[T], float] = lambda _: 0.0,
    ) -> Generator[T, None, None]:
        """Yield stream items while enforcing budget before each yield."""
        guard = cls(watchdog, tool_name=tool_name, agent_id=agent_id)
        with guard:
            for chunk in stream:
                try:
                    guard.record_chunk(
                        token_delta=token_extractor(chunk),
                        cost_delta=cost_extractor(chunk),
                    )
                except BudgetExhaustedError:
                    raise
                yield chunk

    @classmethod
    async def awrap_async_stream(
        cls,
        stream: AsyncIterable[T],
        watchdog: BudgetWatchdog,
        *,
        tool_name: str,
        agent_id: str,
        token_extractor: Callable[[T], int] = lambda _: 1,
        cost_extractor: Callable[[T], float] = lambda _: 0.0,
    ) -> AsyncGenerator[T, None]:
        """Async-yield stream items while enforcing budget before each yield."""
        guard = cls(watchdog, tool_name=tool_name, agent_id=agent_id)
        async with guard:
            async for chunk in stream:
                try:
                    await guard.arecord_chunk(
                        token_delta=token_extractor(chunk),
                        cost_delta=cost_extractor(chunk),
                    )
                except BudgetExhaustedError:
                    raise
                yield chunk


def _cost_to_micros(cost_delta: float) -> int:
    return round(cost_delta * 1_000_000)
