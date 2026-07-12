"""Budget watchdog — enforces agent resource budgets with concurrency-safe reservations.

Budget consumption semantics (fixed):
- consume_call(): once per resilient()/async_resilient() invocation.
- failure_budget: incremented once per failed attempt, including retries.
- Token/cost: check-and-reserve is one lock-protected atomic operation.
  Calls that cannot reserve capacity fail before starting upstream work,
  preventing N * delta overspend under contention.
"""

from __future__ import annotations

import logging
import threading
import time

from agentic_exception_sdk.budget.models import AgentBudget
from agentic_exception_sdk.taxonomy.errors import BudgetExhaustedError

_log = logging.getLogger(__name__)

__all__ = ["BudgetWatchdog"]


class BudgetWatchdog:
    """Tracks and enforces agent resource budgets with concurrency-safe atomic reservations.

    Sync and async paths share a threading.RLock so mixed-concurrency callers
    cannot race with each other.

    All check-and-reserve operations are atomic within their respective lock so
    that concurrent async_resilient() executions cannot overspend the budget
    through races (N * delta overspend prevention).

    Args:
        budget: The AgentBudget configuration specifying ceiling values.
    """

    def __init__(self, budget: AgentBudget) -> None:
        self._budget = budget
        self._start_time: float = time.monotonic()
        self._call_count: int = 0
        self._failure_count: int = 0
        self._input_tokens_used: int = 0
        self._output_tokens_used: int = 0
        self._cost_micros_used: int = 0
        self._lock = threading.RLock()

    def consume_call(self) -> None:
        """Record one tool call invocation and check max_tool_calls ceiling.

        This must be called once per resilient()/async_resilient() invocation.

        Raises:
            BudgetExhaustedError: If max_tool_calls ceiling is exceeded.
            BudgetWarningError: Not currently raised here (implemented at soft-limit).
        """
        with self._lock:
            self._call_count += 1
            if (
                self._budget.max_tool_calls is not None
                and self._call_count > self._budget.max_tool_calls
            ):
                raise BudgetExhaustedError(
                    f"tool call budget exhausted: {self._call_count} > "
                    f"{self._budget.max_tool_calls} max_tool_calls"
                )

    def record_failure(self) -> None:
        """Increment failure count once per failed attempt including retries.

        Raises:
            BudgetExhaustedError: If failure_budget ceiling is exceeded.
        """
        with self._lock:
            self._failure_count += 1
            if (
                self._budget.failure_budget is not None
                and self._failure_count >= self._budget.failure_budget
            ):
                raise BudgetExhaustedError(
                    f"failure budget exhausted: {self._failure_count} failures >= "
                    f"{self._budget.failure_budget} failure_budget"
                )

    def check_time(self) -> None:
        """Check wall-clock time budget.

        Raises:
            BudgetExhaustedError: If max_seconds ceiling has been exceeded.
        """
        if self._budget.max_seconds is None:
            return
        elapsed = time.monotonic() - self._start_time
        if elapsed > self._budget.max_seconds:
            raise BudgetExhaustedError(
                f"time budget exhausted: {elapsed:.2f}s > {self._budget.max_seconds}s max_seconds"
            )

    def check_before_sleep(self, sleep_seconds: float) -> None:
        """Check whether the remaining time budget can accommodate a proposed sleep.

        Called by the retry policy before each backoff sleep.

        Args:
            sleep_seconds: Proposed sleep duration in seconds.

        Raises:
            BudgetExhaustedError: If the remaining time budget cannot cover the sleep.
        """
        if self._budget.max_seconds is None:
            return
        elapsed = time.monotonic() - self._start_time
        remaining = self._budget.max_seconds - elapsed
        if sleep_seconds > remaining:
            raise BudgetExhaustedError(
                f"retry sleep ({sleep_seconds:.2f}s) would exceed remaining time budget "
                f"({remaining:.2f}s)"
            )

    def reserve_tokens(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros_usd: int = 0,
    ) -> None:
        """Atomically check and reserve token/cost capacity before an LLM call.

        Check-and-reserve is one lock-protected operation to prevent N * delta
        overspend under concurrent async calls.

        Args:
            input_tokens: Input tokens to reserve.
            output_tokens: Output tokens to reserve.
            cost_micros_usd: Cost in integer micro-USD to reserve.

        Raises:
            BudgetExhaustedError: If any ceiling would be exceeded.
        """
        with self._lock:
            self._check_token_ceilings(input_tokens, output_tokens, cost_micros_usd)
            self._input_tokens_used += input_tokens
            self._output_tokens_used += output_tokens
            self._cost_micros_used += cost_micros_usd

    def _check_token_ceilings(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_micros_usd: int,
    ) -> None:
        """Check token/cost ceilings. Must be called while holding the lock."""
        if (
            self._budget.max_input_tokens is not None
            and self._input_tokens_used + input_tokens > self._budget.max_input_tokens
        ):
            raise BudgetExhaustedError(
                f"input token budget exhausted: "
                f"{self._input_tokens_used + input_tokens} > {self._budget.max_input_tokens}"
            )
        if (
            self._budget.max_output_tokens is not None
            and self._output_tokens_used + output_tokens > self._budget.max_output_tokens
        ):
            raise BudgetExhaustedError(
                f"output token budget exhausted: "
                f"{self._output_tokens_used + output_tokens} > {self._budget.max_output_tokens}"
            )
        if (
            self._budget.max_total_cost_micros_usd is not None
            and self._cost_micros_used + cost_micros_usd > self._budget.max_total_cost_micros_usd
        ):
            raise BudgetExhaustedError(
                f"cost budget exhausted: "
                f"{self._cost_micros_used + cost_micros_usd} > "
                f"{self._budget.max_total_cost_micros_usd} micro-USD"
            )

    async def async_reserve_tokens(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros_usd: int = 0,
    ) -> None:
        """Async-safe version of reserve_tokens using the shared thread lock.

        Check-and-reserve is one asyncio.Lock-protected operation.

        Args:
            input_tokens: Input tokens to reserve.
            output_tokens: Output tokens to reserve.
            cost_micros_usd: Cost in integer micro-USD to reserve.

        Raises:
            BudgetExhaustedError: If any ceiling would be exceeded.
        """
        with self._lock:
            self._check_token_ceilings(input_tokens, output_tokens, cost_micros_usd)
            self._input_tokens_used += input_tokens
            self._output_tokens_used += output_tokens
            self._cost_micros_used += cost_micros_usd

    def account_stream_delta(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros_usd: int = 0,
    ) -> None:
        """Update token/cost from a streaming LLM response delta.

        Call this from the streaming consumer on each chunk. Raises
        BudgetExhaustedError immediately if the incremental delta would exceed
        the remaining reservation — aborting the stream early rather than waiting
        for the full response.

        Args:
            input_tokens: Incremental input token delta.
            output_tokens: Incremental output token delta.
            cost_micros_usd: Incremental cost in integer micro-USD.

        Raises:
            BudgetExhaustedError: If any ceiling is exceeded mid-stream.
        """
        with self._lock:
            self._check_token_ceilings(input_tokens, output_tokens, cost_micros_usd)
            self._input_tokens_used += input_tokens
            self._output_tokens_used += output_tokens
            self._cost_micros_used += cost_micros_usd

    def snapshot(self) -> dict[str, int | float | None]:
        """Return a thread-safe snapshot of counters and ceilings.

        Returns:
            A plain dict of current usage counters, elapsed seconds, and budget
            ceilings. Monetary values are integer micro-USD.
        """
        with self._lock:
            elapsed = time.monotonic() - self._start_time
            return {
                "call_count": self._call_count,
                "failure_count": self._failure_count,
                "input_tokens_used": self._input_tokens_used,
                "output_tokens_used": self._output_tokens_used,
                "cost_micros_used": self._cost_micros_used,
                "elapsed_seconds": elapsed,
                "max_tool_calls": self._budget.max_tool_calls,
                "failure_budget": self._budget.failure_budget,
                "max_input_tokens": self._budget.max_input_tokens,
                "max_output_tokens": self._budget.max_output_tokens,
                "max_total_cost_micros_usd": self._budget.max_total_cost_micros_usd,
                "max_seconds": self._budget.max_seconds,
            }

    @property
    def call_count(self) -> int:
        """Total tool call invocations recorded."""
        with self._lock:
            return self._call_count

    @property
    def failure_count(self) -> int:
        """Total failed attempts recorded."""
        with self._lock:
            return self._failure_count

    @property
    def input_tokens_used(self) -> int:
        """Total input tokens consumed."""
        with self._lock:
            return self._input_tokens_used

    @property
    def output_tokens_used(self) -> int:
        """Total output tokens consumed."""
        with self._lock:
            return self._output_tokens_used

    @property
    def cost_micros_used(self) -> int:
        """Total cost consumed in integer micro-USD."""
        with self._lock:
            return self._cost_micros_used
